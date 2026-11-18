"""Incremental load: mock vendor API -> raw (as received) -> staging (typed, deduped, mapped) -> marts.

Run (from p3_cx_pipeline/):
  py -m pipeline.run                         # one daily run, 3-day look-back
  py -m pipeline.run --replay-days 14        # simulate 14 daily runs ending now (demo)
  py -m pipeline.run --backfill 2026-11-01 2026-11-05   # re-read a date range, snapshot staging first
Env: P3_DB (sqlite path), PII_SALT (hash salt), MOCK_DRIFT (inject schema drift, see mock_api.drift_from_env).
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
import sys
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from mock_api import IST, UTC, MockAPI, RateLimited, drift_from_env  # noqa: E402

DB = pathlib.Path(os.environ.get("P3_DB", HERE.parent / "data" / "p3.db"))
SALT = os.environ.get("PII_SALT", "dev-only-salt")  # set a real one in the secret store
CONTRACTS = {k: v for k, v in json.loads((HERE / "contracts.json").read_text()).items() if not k.startswith("_")}
CREATED = {"ticketing": "created_at", "orders": "created_at", "telephony": "started_at", "bot": "started_at"}
LAUNCH = dt.datetime(2020, 1, 1, tzinfo=UTC)
BACKOFF_S = 0.2  # first retry wait; doubles each attempt
FMT = "%Y-%m-%d %H:%M:%S"


# ---- setup -----------------------------------------------------------------------------
def stg_columns(source):
    cols = []
    for f, spec in CONTRACTS[source]["fields"].items():
        if spec.get("pii"):
            cols.append((f + "_hash", "TEXT"))
            continue
        cols.append((f, "INTEGER" if spec["type"] == "int" else "TEXT"))
        if "map" in spec:
            cols.append((spec["as"], "TEXT"))
    return cols + [("row_hash", "TEXT")]


def connect(path=None):
    path = pathlib.Path(path or DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS run_log (
      run_id TEXT, table_name TEXT, mode TEXT, started_at_utc TIMESTAMP, finished_at_utc TIMESTAMP,
      rows_in INT, rows_new INT, rows_changed INT, rows_rejected INT,
      watermark_before TIMESTAMP, watermark_after TIMESTAMP, status TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS alerts (run_id TEXT, at_utc TIMESTAMP, level TEXT, table_name TEXT, message TEXT);
    CREATE TABLE IF NOT EXISTS dq_results (run_id TEXT, checked_at_utc TIMESTAMP, check_name TEXT, table_name TEXT,
      value REAL, threshold REAL, status TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS recon_results (run_id TEXT, day TEXT, table_name TEXT, team TEXT,
      src_count INT, stg_count INT, diff INT, cause TEXT);
    CREATE TABLE IF NOT EXISTS mart_daily (day TEXT, metric TEXT, numerator REAL, denominator REAL, value REAL,
      provisional INT, PRIMARY KEY (day, metric));
    CREATE TABLE IF NOT EXISTS mart_daily_history (as_of_utc TIMESTAMP, day TEXT, metric TEXT, value REAL);
    CREATE TABLE IF NOT EXISTS map_values (map TEXT, source TEXT, platform_value TEXT, analytics_value TEXT);
    """)
    for s, c in CONTRACTS.items():
        cols = ", ".join(f"{n} {t}" for n, t in stg_columns(s))
        con.execute(f"CREATE TABLE IF NOT EXISTS raw_{s} (run_id TEXT, loaded_at_utc TIMESTAMP, record_key TEXT, payload TEXT)")
        con.execute(f"CREATE TABLE IF NOT EXISTS stg_{s} ({cols}, PRIMARY KEY ({c['key']}))")
    ist = "'+330 minutes'"
    con.executescript(f"""
    CREATE VIEW IF NOT EXISTS mart_tickets AS
      SELECT ticket_id, date(created_at, {ist}) AS created_date_ist, date(resolved_at, {ist}) AS resolved_date_ist,
             status, status_group, channel_group, team, order_ref,
             order_ref IN (SELECT order_id FROM stg_orders) AS has_order, requester_email_hash
      FROM stg_ticketing;
    CREATE VIEW IF NOT EXISTS mart_orders AS
      SELECT order_id, date(created_at, {ist}) AS created_date_ist, date(delivered_at, {ist}) AS delivered_date_ist,
             status, status_group, promised_at, delivered_at, date(delivered_at, {ist}) > date(promised_at, {ist}) AS is_late
      FROM stg_orders;
    CREATE VIEW IF NOT EXISTS mart_calls AS
      SELECT call_id, date(started_at, {ist}) AS start_date_ist, outcome, team, duration_s, caller_phone_hash
      FROM stg_telephony;
    CREATE VIEW IF NOT EXISTS mart_conversations AS
      SELECT conversation_id, date(started_at, {ist}) AS start_date_ist, channel_group, outcome_group
      FROM stg_bot;
    """)
    # mapping tables live in the repo as CSV; reload every run so a merged PR takes effect
    with con:
        con.execute("DELETE FROM map_values")
        for f in sorted((HERE / "maps").glob("*.csv")):
            with open(f, newline="") as fh:
                con.executemany("INSERT INTO map_values VALUES (?,?,?,?)",
                                [(f.stem, r["source"], r["platform_value"], r["analytics_value"]) for r in csv.DictReader(fh)])
    return con


def load_maps(con):
    maps = {}
    for r in con.execute("SELECT * FROM map_values"):
        maps.setdefault(r["map"], {})[(r["source"], r["platform_value"])] = r["analytics_value"]
    return maps


def alert(con, run_id, level, table, msg):
    con.execute("INSERT INTO alerts VALUES (?,?,?,?,?)", (run_id, dt.datetime.now(UTC).strftime(FMT), level, table, msg))
    con.commit()
    print(f"ALERT [{level}] {table}: {msg}", file=sys.stderr)


# ---- contract + transform --------------------------------------------------------------
def parse_dt(v, fmt):
    if fmt == "epoch_ms":
        t = dt.datetime.fromtimestamp(v / 1000, UTC)
    elif fmt == "dmy_ist":
        t = dt.datetime.strptime(v, "%d/%m/%Y %H:%M").replace(tzinfo=IST)
    else:
        t = dt.datetime.fromisoformat(v)
    return t.astimezone(UTC).strftime(FMT)


def type_ok(v, spec):
    if spec["type"] == "int" or spec.get("format") == "epoch_ms":
        return isinstance(v, int) and not isinstance(v, bool)
    if not isinstance(v, str):
        return False
    if spec["type"] == "datetime":
        try:
            parse_dt(v, spec["format"])
        except ValueError:
            return False
    return True


def check_contract(source, records):
    """Compare the payload to the contract. Returns (errors -> fail the run, warnings -> load known fields)."""
    fields = CONTRACTS[source]["fields"]
    missing, extra, bad_type = set(), set(), {}
    for r in records:
        missing |= fields.keys() - r.keys()
        extra |= r.keys() - fields.keys()
        for f, spec in fields.items():
            if r.get(f) not in (None, "") and not type_ok(r[f], spec):
                bad_type[f] = type(r[f]).__name__
    errors = [f"field missing or renamed: {f}" for f in sorted(missing)]
    errors += [f"type changed: {f} is now {t}, contract says {fields[f]['type']}" for f, t in sorted(bad_type.items())]
    warnings = [f"new field (not loaded): {f}" for f in sorted(extra)]
    return errors, warnings


def transform(source, rec, maps):
    """One payload -> one staging row (typed, UTC, mapped, PII hashed). None = rejected (required field blank)."""
    out = {}
    for f, spec in CONTRACTS[source]["fields"].items():
        v = rec.get(f)
        if v in (None, ""):
            if spec["required"]:
                return None
            v = None
        elif spec["type"] == "datetime":
            v = parse_dt(v, spec["format"])
        if spec.get("pii"):
            out[f + "_hash"] = v and hashlib.sha256((SALT + v).encode()).hexdigest()[:16]
            continue
        out[f] = v
        if "map" in spec:
            m = maps[spec["map"]]
            out[spec["as"]] = None if v is None else m.get((source, v)) or m.get(("*", v)) or "UNMAPPED"
    out["row_hash"] = hashlib.md5(json.dumps(out, sort_keys=True).encode()).hexdigest()
    return out


# ---- load ------------------------------------------------------------------------------
def fetch_all(api, source, since, until):
    recs, cursor = [], 0
    while cursor is not None:
        for attempt in range(6):
            try:
                page = api.fetch_page(source, since, until, cursor)
                break
            except RateLimited:
                if attempt == 5:
                    raise
                time.sleep(BACKOFF_S * 2 ** attempt)
        recs += page["records"]
        cursor = page["next_cursor"]
    return recs


def last_watermark(con, source):
    v = con.execute("SELECT MAX(watermark_after) FROM run_log WHERE table_name=? AND status!='failed'", (source,)).fetchone()[0]
    return v and dt.datetime.strptime(v, FMT).replace(tzinfo=UTC)


def load_source(con, api, source, run_id, now, lookback_days, backfill=None):
    key = CONTRACTS[source]["key"]
    log = dict(run_id=run_id, table_name=source, mode="backfill" if backfill else "incremental",
               started_at_utc=dt.datetime.now(UTC).strftime(FMT), rows_in=0, rows_new=0, rows_changed=0,
               rows_rejected=0, status="success", error=None)
    wm = last_watermark(con, source)
    if backfill:
        since, until = (dt.datetime.combine(d, dt.time(), IST) for d in (backfill[0], backfill[1] + dt.timedelta(days=1)))
        con.execute(f"DROP TABLE IF EXISTS bak_stg_{source}")
        con.execute(f"CREATE TABLE bak_stg_{source} AS SELECT * FROM stg_{source}")  # back up before overwrite
    else:
        since, until = (wm - dt.timedelta(days=lookback_days) if wm else None), now
    try:
        recs = fetch_all(api, source, since, until)
        errors, warnings = check_contract(source, recs)
    except Exception as e:  # noqa: BLE001 - any source failure is logged and alerted, never half-loaded
        recs, errors, warnings = [], [f"fetch failed: {e!r}"], []
    log["rows_in"] = len(recs)
    for w in warnings:
        alert(con, run_id, "warn", source, w)
    if errors:
        log.update(status="failed", error="; ".join(errors), watermark_before=wm and wm.strftime(FMT), watermark_after=None)
        alert(con, run_id, "fail", source, log["error"] + " -> run failed, nothing loaded")
    else:
        maps = load_maps(con)
        cols = [c for c, _ in stg_columns(source)]
        upsert = (f"INSERT INTO stg_{source} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
                  f"ON CONFLICT({key}) DO UPDATE SET {','.join(f'{c}=excluded.{c}' for c in cols)} "
                  f"WHERE excluded.updated_at >= stg_{source}.updated_at")
        loaded_at = now.strftime(FMT)
        with con:  # raw + staging in one transaction
            known = dict(con.execute(f"SELECT {key}, row_hash FROM stg_{source}").fetchall())
            con.executemany(f"INSERT INTO raw_{source} VALUES (?,?,?,?)",
                            [(run_id, loaded_at, str(r.get(key)), json.dumps(r)) for r in recs])
            for r in recs:
                row = transform(source, r, maps)
                if row is None:
                    log["rows_rejected"] += 1
                    continue
                old = known.get(row[key])
                if old == row["row_hash"]:
                    continue
                log["rows_new" if old is None else "rows_changed"] += 1
                con.execute(upsert, [row[c] for c in cols])
        top = max((parse_dt(r["updated_at"], "iso") for r in recs), default=None)
        log.update(watermark_before=wm and wm.strftime(FMT), watermark_after=max(filter(None, [top, wm and wm.strftime(FMT)]), default=None))
        if log["rows_rejected"]:
            alert(con, run_id, "warn", source, f"{log['rows_rejected']} rows rejected (required field blank)")
        for f, spec in CONTRACTS[source]["fields"].items():
            if "map" in spec:
                bad = con.execute(f"SELECT {f}, COUNT(*) n FROM stg_{source} WHERE {spec['as']}='UNMAPPED' "
                                  f"GROUP BY {f} ORDER BY n DESC LIMIT 5").fetchall()
                if bad:
                    alert(con, run_id, "warn", source, f"UNMAPPED {f} values (add to {spec['map']}.csv): "
                          + ", ".join(f"{b[0]} x{b[1]}" for b in bad))
    log["finished_at_utc"] = dt.datetime.now(UTC).strftime(FMT)
    con.execute(f"INSERT INTO run_log ({','.join(log)}) VALUES ({','.join('?' * len(log))})", list(log.values()))
    con.commit()
    return log


def build_marts(con, now):
    from pipeline import metrics
    rows = {s: [dict(r) for r in con.execute(f"SELECT * FROM stg_{s}")] for s in CONTRACTS}
    today = now.astimezone(IST).date()
    with con:
        con.execute("DELETE FROM mart_daily")
        for name, m in metrics.METRICS.items():
            for day, (num, den) in sorted(metrics.compute(rows[m["source"]], name).items()):
                prov = (today - dt.date.fromisoformat(day)).days < m.get("provisional_days", 0)
                val = metrics.value(name, num, den)
                con.execute("INSERT INTO mart_daily VALUES (?,?,?,?,?,?)", (day, name, num, den, val, prov))
                con.execute("INSERT INTO mart_daily_history VALUES (?,?,?,?)", (now.strftime(FMT), day, name, val))


def run(con, api, now, lookback_days=3, backfill=None, sources=None):
    """One pipeline run. Returns 0 if every source loaded, 1 if any failed."""
    run_id = f"{now:%Y%m%dT%H%M}-{uuid.uuid4().hex[:6]}"
    logs = [load_source(con, api, s, run_id, now, lookback_days, backfill) for s in sources or CONTRACTS]
    build_marts(con, now)
    for g in logs:
        print(f"{run_id} {g['table_name']:<10} {g['status']:<8} in={g['rows_in']} new={g['rows_new']} "
              f"changed={g['rows_changed']} rejected={g['rows_rejected']} wm={g['watermark_after']}")
    return int(any(g["status"] == "failed" for g in logs))


def parse_now(text):
    return dt.datetime.fromisoformat(text).astimezone(UTC) if text else dt.datetime.now(UTC).replace(second=0, microsecond=0)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--lookback-days", type=int, default=3)
    p.add_argument("--as-of", help="pretend 'now' is this UTC time (ISO), default: real now")
    p.add_argument("--backfill", nargs=2, type=dt.date.fromisoformat, metavar=("FROM", "TO"), help="IST dates")
    p.add_argument("--replay-days", type=int, default=0, help="run once per day for N days ending at --as-of")
    a = p.parse_args(argv)
    now = parse_now(a.as_of)
    con = connect()
    api = MockAPI(now, drift=drift_from_env(os.environ.get("MOCK_DRIFT")))
    rc = 0
    for back in range(a.replay_days, -1, -1):
        api.now = now - dt.timedelta(days=back)
        rc |= run(con, api, api.now, a.lookback_days, a.backfill)
    return rc


if __name__ == "__main__":
    sys.exit(main())
