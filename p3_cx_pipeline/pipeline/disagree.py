"""Why do two dashboards disagree?

Give it the metric, the date range and the other dashboard's number (B). It recomputes our number (A,
built on metrics.md) changing ONE thing at a time and reports how much of the gap each cause explains.
  timezone     UTC dates instead of IST
  late_edits   data as it stood when B was produced (from raw, which is never edited)
  duplicates   raw rows without dedup (every re-read and every version counted)
  denominator  the population a typical other dashboard uses (metrics.METRICS[...]['alt'])
  filters      B's filters (channel_group=whatsapp, team=refunds, ...)

Run (from p3_cx_pipeline/):
  py -m pipeline.disagree --metric resolved_tickets --from 2026-11-10 --to 2026-11-14 --b 640 \
     --b-as-of 2026-11-15T00:30:00+00:00 [--b-filter channel_group=whatsapp]
"""
import argparse
import datetime as dt
import itertools
import json
import sys

from pipeline import metrics
from pipeline.run import CONTRACTS, FMT, IST, connect, load_maps, parse_now, transform

FIX = {
    "timezone": "Anchor both dashboards on IST dates (metrics.md); convert UTC to IST once, in the mart.",
    "late_edits": "Read the daily mart (refreshed with a 3-day look-back) and show recent days as provisional.",
    "duplicates": "Read staging or the mart (one row per key), never the raw export.",
    "denominator": "Agree one population in metrics.md and rebuild the other dashboard on it.",
    "filters": "Remove the filter, or label the dashboard with it so nobody compares it to the total.",
}


def raw_rows(con, source, as_of=None, dedup=True):
    sql, args = f"SELECT payload FROM raw_{source}", ()
    if as_of:
        sql, args = sql + " WHERE loaded_at_utc <= ?", (as_of.strftime(FMT),)
    maps = load_maps(con)
    rows = [r for (p,) in con.execute(sql, args) if (r := transform(source, json.loads(p), maps))]
    if not dedup:
        return rows
    key, latest = CONTRACTS[source]["key"], {}
    for r in rows:
        if r[key] not in latest or r["updated_at"] >= latest[r[key]]["updated_at"]:
            latest[r[key]] = r
    return list(latest.values())


def explain(con, name, start, end, b, b_as_of, filters=None):
    """Returns (A, per-cause rows, causes chosen, value with chosen causes applied).
    Picks the combination of causes that lands closest to B, then credits each chosen cause with its
    marginal move. A cause that overshoots (e.g. raw duplicates when B is only slightly higher) is not chosen."""
    src = metrics.METRICS[name]["source"]
    stg = [dict(r) for r in con.execute(f"SELECT * FROM stg_{src}")]
    raw = {}

    def calc(causes):
        rows = stg
        if "late_edits" in causes or "duplicates" in causes:
            k = ("late_edits" in causes, "duplicates" not in causes)
            if k not in raw:
                raw[k] = raw_rows(con, src, b_as_of if k[0] else None, dedup=k[1])
            rows = raw[k]
        by_day = metrics.compute(rows, name, "UTC" if "timezone" in causes else "IST",
                                 "alt" if "denominator" in causes else "ours", filters if "filters" in causes else None)
        return metrics.total(name, by_day, start, end) or 0

    what = {"timezone": "UTC dates instead of IST",
            "late_edits": f"data as of {b_as_of:%Y-%m-%d %H:%M} UTC",
            "duplicates": "no dedup on key (every re-read and version counted)",
            "denominator": metrics.METRICS[name]["alt"]["note"]}
    if filters:
        what["filters"] = ", ".join(f"{k}={v}" for k, v in filters.items())
    a = calc(())
    gap = b - a
    alone = {c: calc([c]) for c in what}
    # closest combination of causes to B (ties -> fewer causes); 5 causes = 32 combinations
    combos = [c for k in range(len(what) + 1) for c in itertools.combinations(what, k)]
    chosen = list(min(combos, key=lambda c: (round(abs(b - calc(c)), 9), len(c))))
    # credit: walk the chosen causes, biggest single effect first, each gets its marginal move
    chosen.sort(key=lambda c: -abs(alone[c] - a))
    credit, cur = {}, a
    for i, c in enumerate(chosen):
        nxt = calc(chosen[:i + 1])
        credit[c], cur = nxt - cur, nxt
    out = []
    for c, w in what.items():
        out.append(dict(cause=c, what=w, value=alone[c], moves=alone[c] - a,
                        share=credit.get(c, 0) / gap if gap else 0, credited=c in credit))
    out.sort(key=lambda r: (-r["share"], -abs(r["moves"])))
    return a, out, chosen, cur


def main(argv=None):
    p = argparse.ArgumentParser(description="Explain the gap between two dashboards")
    p.add_argument("--metric", required=True, choices=list(metrics.METRICS))
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p.add_argument("--b", type=float, required=True, help="the other dashboard's number")
    p.add_argument("--a", type=float, help="our dashboard's number (default: computed from the mart definition)")
    p.add_argument("--b-as-of", help="when B was produced, UTC ISO (default: 06:00 IST the day after --to)")
    p.add_argument("--b-filter", action="append", default=[], help="k=v filter applied on B, repeatable")
    a = p.parse_args(argv)
    b_as_of = parse_now(a.b_as_of) if a.b_as_of else dt.datetime.combine(
        dt.date.fromisoformat(a.end) + dt.timedelta(days=1), dt.time(6), IST).astimezone(dt.timezone.utc)
    filters = dict(f.split("=", 1) for f in a.b_filter)

    con = connect()
    ours, rows, helpful, together = explain(con, a.metric, a.start, a.end, a.b, b_as_of, filters)
    rate = metrics.METRICS[a.metric]["kind"] == "rate"
    unit = (lambda x: f"{x * 100:+.2f} pp") if rate else (lambda x: f"{x:+,.0f}")
    gap = a.b - ours

    print(f"## Why `{a.metric}` differs, {a.start} to {a.end} (IST)\n")
    if a.a is not None and a.a != ours:
        print(f"> Note: dashboard A shows {a.a}, the mart definition gives {ours}. Using {ours}.\n")
    print(f"- A (metrics.md definition): **{ours}**\n- B (other dashboard): **{a.b}**\n- Gap B - A: **{unit(gap)}**\n")
    print("| Rank | Cause | Recomputed with | Value alone | Moves A by | Gap explained | Fix |")
    print("|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        print(f"| {i} | {r['cause']} | {r['what']} | {r['value']} | {unit(r['moves'])} | "
              f"{f'{r['share']:.0%}' if r['credited'] else 'not a cause'} | {FIX[r['cause']] if r['credited'] else '-'} |")
    if gap:
        share = (together - ours) / gap
        print(f"\nCauses applied together ({', '.join(helpful) or 'none'}): **{together}**, "
              f"explains **{share:.0%}** of the gap; unexplained **{unit(a.b - together)}**.")
        if filters:
            print(f"\nFilters on B: {', '.join(f'{k}={v}' for k, v in filters.items())}. Our dashboard has none.")
    else:
        print("\nNo gap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
