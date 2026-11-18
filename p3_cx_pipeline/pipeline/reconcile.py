"""Daily reconciliation.
1. Staging vs the source's own count, per IST day and queue (last 14 days). Any difference -> recon_results,
   alert, exit 1; the owner writes the cause into recon_results.cause.
2. Late records: each day's metric "as reported" (first run after the day closed) vs "as of today".
Run (from p3_cx_pipeline/): py -m pipeline.reconcile [--as-of ISO]
"""
import argparse
import datetime as dt
import os
import sys

from mock_api import MockAPI, drift_from_env
from pipeline.run import CONTRACTS, CREATED, FMT, IST, alert, connect, parse_now

DAYS = 14


def reconcile_counts(con, api, now):
    run_id = con.execute("SELECT run_id FROM run_log ORDER BY started_at_utc DESC LIMIT 1").fetchone()[0]
    first = (now.astimezone(IST).date() - dt.timedelta(days=DAYS)).isoformat()
    diffs = []
    for s in CONTRACTS:
        team = "queue" if "queue" in CONTRACTS[s]["fields"] else "'all'"
        con.execute("DROP TABLE IF EXISTS temp.source_daily")
        con.execute("CREATE TEMP TABLE source_daily (day TEXT, team TEXT, src_count INT)")
        con.executemany("INSERT INTO source_daily VALUES (?,?,?)", [(d, t, n) for (d, t), n in api.daily_counts(s).items()])
        rows = con.execute(f"""
            WITH stg_daily AS (
              SELECT date({CREATED[s]}, '+330 minutes') AS day, {team} AS team, COUNT(*) AS stg_count
              FROM stg_{s} GROUP BY 1, 2)
            SELECT day, team, COALESCE(s.src_count, 0) AS src_count, COALESCE(d.stg_count, 0) AS stg_count,
                   COALESCE(d.stg_count, 0) - COALESCE(s.src_count, 0) AS diff
            FROM source_daily s FULL OUTER JOIN stg_daily d USING (day, team)
            WHERE day >= ? AND ABS(COALESCE(d.stg_count, 0) - COALESCE(s.src_count, 0)) > 0
            ORDER BY day, team""", (first,)).fetchall()
        diffs += [(run_id, r["day"], s, r["team"], r["src_count"], r["stg_count"], r["diff"], None) for r in rows]
    with con:
        con.executemany("INSERT INTO recon_results VALUES (?,?,?,?,?,?,?,?)", diffs)
    for d in diffs:
        alert(con, run_id, "fail", d[2], f"reconciliation {d[1]} {d[3]}: source {d[4]} vs staging {d[5]} (diff {d[6]})"
              " -> investigate, write cause in recon_results")
    return diffs


def late_changes(con, now, days=7):
    """Per day and metric: value in the first snapshot taken after the day closed (IST) vs value now."""
    out = []
    today = now.astimezone(IST).date()
    for i in range(days, 0, -1):
        d = (today - dt.timedelta(days=i)).isoformat()
        closed = (dt.datetime.combine(dt.date.fromisoformat(d) + dt.timedelta(days=1), dt.time(), IST)
                  .astimezone(dt.timezone.utc).strftime(FMT))
        for r in con.execute("""
            SELECT h.metric, h.value AS reported, m.value AS now_value, h.as_of_utc
            FROM mart_daily_history h JOIN mart_daily m ON m.day = h.day AND m.metric = h.metric
            WHERE h.day = ? AND h.as_of_utc = (SELECT MIN(as_of_utc) FROM mart_daily_history
                                               WHERE day = ? AND as_of_utc >= ?)""", (d, d, closed)):
            if r["reported"] != r["now_value"]:
                out.append((d, r["metric"], r["reported"], r["now_value"], r["as_of_utc"]))
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--as-of")
    a = p.parse_args(argv)
    now = parse_now(a.as_of)
    con = connect()
    diffs = reconcile_counts(con, MockAPI(now, drift=drift_from_env(os.environ.get("MOCK_DRIFT"))), now)
    print(f"Source vs staging, last {DAYS} days: {len(diffs)} day/team rows differ")
    for d in diffs:
        print(f"  {d[1]} {d[2]:<10} {d[3]:<12} source={d[4]} staging={d[5]} diff={d[6]}")
    print("\nLate records: metric as reported vs as of today (last 7 days)")
    for d, m, rep, cur, at in late_changes(con, now):
        print(f"  {d} {m:<18} reported={rep} (at {at}) now={cur}")
    return int(bool(diffs))


if __name__ == "__main__":
    sys.exit(main())
