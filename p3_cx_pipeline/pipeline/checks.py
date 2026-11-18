"""Data-quality checks on staging -> dq_results. Exit 1 if any check fails.
Run (from p3_cx_pipeline/): py -m pipeline.checks [--as-of ISO]
"""
import argparse
import datetime as dt
import sys

from pipeline.run import CONTRACTS, CREATED, FMT, IST, LAUNCH, alert, connect, parse_now

FRESH_HOURS = 26     # daily schedule + 2 h slack
UNMAPPED_MAX = 0.01
DROP_MIN = 0.70      # yesterday vs 4-week same-weekday average


def ist_day(col):
    return f"date({col}, '+330 minutes')"


def run_checks(con, now):
    run_id = con.execute("SELECT run_id FROM run_log ORDER BY started_at_utc DESC LIMIT 1").fetchone()
    run_id = run_id and run_id[0]
    nows = now.strftime(FMT)
    week_ago = (now - dt.timedelta(days=7)).strftime(FMT)
    res = []

    def add(check, table, value, threshold, status, detail=""):
        res.append((run_id, nows, check, table, value, threshold, status, detail))

    for s, c in CONTRACTS.items():
        t, key, made = f"stg_{s}", c["key"], CREATED[s]
        q = lambda sql, *a: con.execute(sql, a).fetchone()[0]  # noqa: E731

        newest = q(f"SELECT MAX(updated_at) FROM {t}")
        lag = (now - dt.datetime.strptime(newest, FMT).replace(tzinfo=now.tzinfo)).total_seconds() / 3600 if newest else 1e9
        add("freshness_hours", s, round(lag, 1), FRESH_HOURS, "fail" if lag > FRESH_HOURS else "pass")

        dups = q(f"SELECT COUNT(*) - COUNT(DISTINCT {key}) FROM {t}")
        add("duplicate_keys", s, dups, 0, "fail" if dups else "pass")

        recent = q(f"SELECT COUNT(*) FROM {t} WHERE updated_at > ?", week_ago) or 1
        for f, spec in c["fields"].items():
            if "blank_warn" in spec:
                share = q(f"SELECT COUNT(*) FROM {t} WHERE updated_at > ? AND {f} IS NULL", week_ago) / recent
                add(f"blank_rate:{f}", s, round(share, 4), spec["blank_warn"], "warn" if share > spec["blank_warn"] else "pass")
            if "map" in spec:
                share = q(f"SELECT COUNT(*) FROM {t} WHERE updated_at > ? AND {spec['as']}='UNMAPPED'", week_ago) / recent
                add(f"unmapped_share:{f}", s, round(share, 4), UNMAPPED_MAX, "warn" if share > UNMAPPED_MAX else "pass")

        yday = (now.astimezone(IST).date() - dt.timedelta(days=1))
        n = q(f"SELECT COUNT(*) FROM {t} WHERE {ist_day(made)} = ?", yday.isoformat())
        prior = [q(f"SELECT COUNT(*) FROM {t} WHERE {ist_day(made)} = ?", (yday - dt.timedelta(weeks=w)).isoformat())
                 for w in range(1, 5)]
        avg = sum(prior) / 4
        ratio = round(n / avg, 3) if avg else None
        add("row_count_vs_4wk_weekday", s, ratio, DROP_MIN, "warn" if ratio is not None and ratio < DROP_MIN else "pass",
            f"{yday}: {n} rows vs avg {avg:.0f}")

        bad = q(f"SELECT COUNT(*) FROM {t} WHERE {made} > ? OR {made} < ? OR updated_at > ?",
                (now + dt.timedelta(hours=1)).strftime(FMT), LAUNCH.strftime(FMT), (now + dt.timedelta(hours=1)).strftime(FMT))
        # promised_at may be in the future by design, so only created/updated are checked
        add("timezone_sanity", s, bad, 0, "fail" if bad else "pass", "timestamps in the future or before launch")

    month_ago = (now - dt.timedelta(days=30)).strftime(FMT)
    tot, joined = con.execute("SELECT COUNT(*), SUM(order_ref IN (SELECT order_id FROM stg_orders)) "
                              "FROM stg_ticketing WHERE created_at > ?", (month_ago,)).fetchone()
    add("ticket_order_coverage", "ticketing", round((joined or 0) / tot, 4) if tot else None, None, "info")

    with con:
        con.executemany("INSERT INTO dq_results VALUES (?,?,?,?,?,?,?,?)", res)
    for r in res:
        if r[6] in ("fail", "warn"):
            alert(con, run_id, r[6], r[3], f"check {r[2]} = {r[4]} (threshold {r[5]}) {r[7]}".strip())
    return res


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--as-of")
    a = p.parse_args(argv)
    res = run_checks(connect(), parse_now(a.as_of))
    for r in res:
        print(f"{r[6]:<5} {r[3]:<10} {r[2]:<30} {r[4]}  (threshold {r[5]}) {r[7]}")
    return int(any(r[6] == "fail" for r in res))


if __name__ == "__main__":
    sys.exit(main())
