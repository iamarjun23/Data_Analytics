"""Weekly impact report (spec 10, 14, 17): treated vs holdout among flagged orders, cost side, false alarms and
misses, lanes, compliance read-backs, monitoring checks. Reads only stored tables, never simulate.EFFECT.
Run: py p2_order_delay_early_warning/report.py   ->  p2_order_delay_early_warning/report.md
"""
import datetime as dt
import math
import pathlib
import sqlite3
import statistics
from collections import Counter, defaultdict

import job
from job import FMT, IST, parse

HERE = pathlib.Path(__file__).resolve().parent
ASSUME = dict(contact_cost=55,     # Rs per ticket handled (same as p5)
              msg_cost=0.4,        # Rs per WhatsApp / SMS / email sent (placeholder)
              callback_min=6,      # agent minutes per callback attempt
              agent_cost_min=9)    # Rs per agent minute (placeholder)
OUTCOME_DAYS = 7                   # pre-registered: tickets within 7 days of the promised date


def ist_day(ts):
    return (parse(ts) + IST).date()


def day_end_utc(d):
    return (dt.datetime.combine(d, dt.time(23, 59, 59)) - IST).strftime(FMT)


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def diff_ci(a, b):
    """Difference in means (a - b) per 100 orders with a 95% normal-approximation interval."""
    d = mean(a) - mean(b)
    se = math.sqrt(statistics.pvariance(a) / len(a) + statistics.pvariance(b) / len(b))
    return 100 * d, 100 * (d - 1.96 * se), 100 * (d + 1.96 * se)


def inr(x):
    return f"₹{x / 1e5:.2f}L" if abs(x) >= 1e5 else f"₹{x:,.0f}"


def pct(x):
    return f"{100 * x:.1f}%"


def main(con=None):
    con = con or sqlite3.connect(HERE.parent / "data" / "p2.db")
    first_run, last_run = con.execute("SELECT MIN(run_ts_utc), MAX(run_ts_utc) FROM job_runs").fetchone()
    p_from = ist_day(first_run) + dt.timedelta(days=3)               # had a fair chance to be flagged in time
    p_to = ist_day(last_run) - dt.timedelta(days=OUTCOME_DAYS)       # 7-day outcome window is complete
    orders = {r[0]: r for r in con.execute(
        "SELECT order_id, value, lane, courier, promised_date, delivered_ts_utc, status FROM orders "
        "WHERE promised_date BETWEEN ? AND ?", (p_from.isoformat(), p_to.isoformat()))}
    first_flag = dict(con.execute("SELECT f.order_id, MIN(r.run_ts_utc) FROM risk_flags f "
                                  "JOIN job_runs r USING (run_id) GROUP BY 1"))
    tix = defaultdict(list)
    for oid, ts, reason, csat in con.execute("SELECT order_id, created_at_utc, reason, csat FROM tickets "
                                             "WHERE order_id IS NOT NULL"):
        tix[oid].append((ts, reason, csat))
    refund, lost = Counter(), set()
    for oid, amt, reason in con.execute("SELECT order_id, amount, reason FROM refunds"):
        refund[oid] += amt
        if reason in ("oos_cancel", "rto"):
            lost.add(oid)

    def is_late(o):
        return o[6] == "delivered" and ist_day(o[5]) > dt.date.fromisoformat(o[4])

    # ---- Treated vs holdout, flagged orders only (intent to treat: treated = flagged and not in holdout)
    arm = {"Treated": [], "Holdout": []}
    for oid, o in orders.items():
        if oid not in first_flag:
            continue
        end = day_end_utc(dt.date.fromisoformat(o[4]) + dt.timedelta(days=OUTCOME_DAYS))
        t_in = [t for t in tix[oid] if first_flag[oid] <= t[0] <= end]
        fday = ist_day(first_flag[oid])
        arm["Holdout" if job.is_holdout(oid) else "Treated"].append(dict(
            id=oid, wismo=sum(r == "wismo" for _, r, _ in t_in), contacts=len(t_in), refund=refund[oid],
            lost=int(oid in lost), csat=[c for *_, c in t_in if c is not None],
            week=fday - dt.timedelta(days=fday.weekday())))
    T, H = arm["Treated"], arm["Holdout"]

    def col(rows, k):
        return [r[k] for r in rows]

    def metrics(rows):
        cs = [c for r in rows for c in r["csat"]]
        return dict(n=len(rows), wismo=100 * mean(col(rows, "wismo")), contacts=100 * mean(col(rows, "contacts")),
                    refund=100 * mean(col(rows, "refund")), lost=mean(col(rows, "lost")), csat=mean(cs), csat_n=len(cs))
    mt, mh = metrics(T), metrics(H)
    w_d = diff_ci(col(T, "wismo"), col(H, "wismo"))
    c_d = diff_ci(col(T, "contacts"), col(H, "contacts"))

    # ---- Cost side
    treated_ids = {r["id"] for r in T}
    sends = Counter()
    for oid, ch, status in con.execute("SELECT order_id, channel, status FROM outreach_log"):
        if oid in treated_ids:
            sends["call" if ch == "call" else "msg"] += 1
            sends[status] += 1
    weeks = (p_to - p_from).days / 7
    n_t = mt["n"]
    wismo_avoided = (mh["wismo"] - mt["wismo"]) / 100 * n_t
    contacts_avoided = (mh["contacts"] - mt["contacts"]) / 100 * n_t
    refund_saved = (mh["refund"] - mt["refund"]) / 100 * n_t
    contact_value = contacts_avoided * ASSUME["contact_cost"]
    run_cost = sends["msg"] * ASSUME["msg_cost"] + sends["call"] * ASSUME["callback_min"] * ASSUME["agent_cost_min"]

    # ---- False alarms and misses (judge the list on both sides)
    flagged = [o for oid, o in orders.items() if oid in first_flag]
    fa = sum(o[6] == "delivered" and not is_late(o) for o in flagged) / len(flagged)
    late = [oid for oid, o in orders.items() if is_late(o)]
    missed = sum(oid not in first_flag or first_flag[oid] > day_end_utc(dt.date.fromisoformat(orders[oid][4]))
                 for oid in late) / len(late)
    flag_share = len(flagged) / len(orders)

    # ---- Lanes (spec section 5 query, over the report window)
    lanes = defaultdict(lambda: [0, 0])
    for o in orders.values():
        if o[6] == "delivered":
            lanes[(o[2], o[3])][0] += 1
            lanes[(o[2], o[3])][1] += is_late(o)
    lane_rows = sorted(((k, n, lt, lt / n) for k, (n, lt) in lanes.items()), key=lambda r: -r[3])
    late_total = sum(r[2] for r in lane_rows)
    top3_share = sum(r[2] for r in lane_rows[:3]) / late_total
    top3_vol = sum(r[1] for r in lane_rows[:3]) / sum(r[1] for r in lane_rows)

    # ---- Compliance read-backs (each should be 0)
    consent_ok = {(a, ch) for a, ch in con.execute("SELECT customer_id, channel FROM consent "
                                                   "WHERE consent_status = 'granted'")}
    acc_of = dict(con.execute("SELECT order_id, account_id FROM orders"))
    dnd_acc = {a for a, in con.execute("SELECT DISTINCT customer_id FROM consent WHERE dnd_flag = 1")}
    log_rows = con.execute("SELECT order_id, sent_at_utc, channel FROM outreach_log").fetchall()
    out_of_hours = sum(not (dt.time(job.QUIET_IST[0]) <= (parse(t) + IST).time() <= dt.time(job.QUIET_IST[1]))
                       for _, t, _ in log_rows)
    holdout_msgd = sum(job.is_holdout(o) for o, _, _ in log_rows)
    no_consent = sum((acc_of[o], ch) not in consent_ok for o, _, ch in log_rows)
    dnd_calls = sum(ch == "call" and acc_of[o] in dnd_acc for o, _, ch in log_rows)
    listed = con.execute("SELECT order_id, account_id, send_at_utc FROM outreach_list JOIN orders USING (order_id) "
                         "WHERE channel IS NOT NULL ORDER BY send_at_utc").fetchall()
    per_order, per_acc, twice_24h, over_cap = defaultdict(list), defaultdict(list), 0, 0
    for oid, a, t in listed:
        t = parse(t)
        twice_24h += any(t - p < dt.timedelta(hours=24) for p in per_order[oid])
        over_cap += sum(t - p < dt.timedelta(days=7) for p in per_acc[a]) >= job.WEEKLY_CAP
        per_order[oid].append(t)
        per_acc[a].append(t)

    # ---- Monitoring
    runs = con.execute("SELECT run_id, run_ts_utc, open_orders, flagged, unmapped, runtime_s, ready_by_cutoff "
                       "FROM job_runs ORDER BY run_id").fetchall()
    morning = [r for r in runs if (parse(r[1]) + IST).hour < 12]
    jumps = []
    for i, r in enumerate(morning):
        prev = [x[3] / x[2] for x in morning[max(0, i - 28):i]]
        if len(prev) >= 7 and r[3] / r[2] > 1.5 * mean(prev):
            jumps.append(f"{ist_day(r[1])} ({pct(r[3] / r[2])} vs {pct(mean(prev))})")
    unknown = con.execute("SELECT status_code, COUNT(*) FROM shipment_events WHERE status_code NOT IN "
                          f"({','.join('?' * len(job.KNOWN))}) GROUP BY 1", sorted(job.KNOWN)).fetchall()
    final_fail = con.execute("SELECT COUNT(*) FROM (SELECT order_id, MAX(status = 'delivered') ok FROM outreach_log "
                             "WHERE channel <> 'call' GROUP BY order_id) WHERE ok = 0").fetchone()[0]
    late_scans = con.execute("SELECT AVG(julianday(ingested_ts_utc) - julianday(event_ts_utc) > 1/24.0) "
                             "FROM shipment_events").fetchone()[0]
    cover = con.execute("SELECT AVG(order_id IS NOT NULL), AVG(CASE WHEN reason = 'wismo' THEN order_id IS NOT NULL "
                        "END) FROM tickets").fetchone()

    # ---- Write it
    md = ["# Order-delay early warning: weekly impact report", "",
          "_Synthetic data, same structure as production. Treatment effect is measured from the 10% holdout; "
          "assumptions at the bottom._", "",
          f"Report window: orders promised **{p_from} to {p_to}** (IST), {len(orders):,} orders, "
          f"{len(flagged):,} flagged ({pct(flag_share)}). Runs: {len(runs)} "
          f"({first_run[:10]} to {last_run[:10]}, 08:00 and 19:30 IST).", "",
          "## Results", "",
          "| Measure | Value |", "|---|---|",
          f"| Share of late orders carried by the worst three lanes | **{pct(top3_share)}** of late deliveries "
          f"on {pct(top3_vol)} of delivered orders |",
          f"| WISMO per 100 flagged orders: treated vs holdout | **{mt['wismo']:.1f} vs {mh['wismo']:.1f}** "
          f"({w_d[0]:+.1f}, 95% CI {w_d[1]:+.1f} to {w_d[2]:+.1f}) |",
          f"| Tickets avoided per week and rupee value | **{contacts_avoided / weeks:,.0f} contacts/week** "
          f"({wismo_avoided / weeks:,.0f} WISMO), {inr(contact_value / weeks)}/week |",
          f"| False alarm rate and miss rate | **{pct(fa)}** of flagged orders arrived on time; "
          f"**{pct(missed)}** of late orders were not flagged before the promised day ended |",
          f"| Run time at 1x, 2x, 3x volume | 1x: {mean([r[5] for r in runs]):.2f}s average per run here; "
          "2x / 3x: see [spike_result.md](spike_result.md) |", "",
          "## Treated vs holdout (flagged orders only)", "",
          "Pre-registered outcome: WISMO tickets per 100 flagged orders, from first flag to 7 days after the promised "
          "date. Holdout = `md5(order_id) % 10 == 0`. Intent to treat: treated includes flagged orders we could not "
          "message (no consent, cap), so the effect is not flattered.", "",
          "| Item | Treated | Holdout | Difference (95% CI) |", "|---|---|---|---|",
          f"| Flagged orders | {mt['n']:,} | {mh['n']:,} | |",
          f"| WISMO tickets per 100 | {mt['wismo']:.1f} | {mh['wismo']:.1f} | "
          f"{w_d[0]:+.1f} ({w_d[1]:+.1f} to {w_d[2]:+.1f}) |",
          f"| All contacts per 100 | {mt['contacts']:.1f} | {mh['contacts']:.1f} | "
          f"{c_d[0]:+.1f} ({c_d[1]:+.1f} to {c_d[2]:+.1f}) |",
          f"| Refunds and credits (₹ per 100 orders) | {inr(mt['refund'])} | {inr(mh['refund'])} | "
          f"{inr(mt['refund'] - mh['refund'])} |",
          f"| Cancelled or RTO | {pct(mt['lost'])} | {pct(mh['lost'])} | {100 * (mt['lost'] - mh['lost']):+.1f} pts |",
          f"| CSAT (1-5, ticket surveys) | {mt['csat']:.2f} (n={mt['csat_n']}) | {mh['csat']:.2f} (n={mh['csat_n']}) "
          f"| {mt['csat'] - mh['csat']:+.2f} |", "",
          "### By week of first flag", "",
          "| Week (Mon, IST) | Treated | Holdout | WISMO/100 treated | WISMO/100 holdout |", "|---|---|---|---|---|"]
    for wk in sorted({r["week"] for r in T + H}):
        t = [r["wismo"] for r in T if r["week"] == wk]
        hh = [r["wismo"] for r in H if r["week"] == wk]
        md.append(f"| {wk} | {len(t):,} | {len(hh):,} | {100 * mean(t):.1f} | {100 * mean(hh):.1f} |")
    md += ["", "_Holdout weeks are small (about 10% of flags); read the weekly rows as a trend, the total as the answer._",
           "", "## Cost side", "",
           "| Item | Value |", "|---|---|",
           f"| Messages sent (incl. retries) / callbacks attempted | {sends['msg']:,} / {sends['call']:,} |",
           f"| Message delivery failures after retry | {final_fail:,} orders |",
           f"| Agent minutes on callbacks | {sends['call'] * ASSUME['callback_min']:,} |",
           f"| Contacts avoided over the window | {contacts_avoided:,.0f} ({inr(contact_value)}) |",
           f"| Refunds and credits avoided | {inr(refund_saved)} |",
           f"| Cost of running outreach (messages + callback minutes) | {inr(run_cost)} |",
           f"| **Net** | **{inr(contact_value + refund_saved - run_cost)}** over {weeks:.1f} weeks |", "",
           "Refunds avoided are mostly out-of-stock customers who took a substitute instead of cancelling; "
           "treat that line as revenue kept, not cost cut.", "",
           "## Where the delay sits (lane analysis)", "",
           "| Lane | Courier | Delivered | Late | Late % |", "|---|---|---|---|---|"]
    md += [f"| {k[0]} | {k[1]} | {n:,} | {lt:,} | {pct(r)} |" for k, n, lt, r in lane_rows]
    md += ["", "Owner: supply chain (courier choice, EDD buffer on these lanes). This project gives the evidence; "
           "it does not switch couriers.", "",
           "## What the list asked CX to do", "", "| Action | Listed | Held out |", "|---|---|---|"]
    for a, n, hn in con.execute("SELECT action, SUM(is_holdout = 0 AND channel IS NOT NULL), SUM(is_holdout) "
                                "FROM outreach_list GROUP BY 1 ORDER BY 2 DESC"):
        md.append(f"| {a} | {n:,} | {hn:,} |")
    md += ["", "| Exclusion reason | Orders | Rows logged |", "|---|---|---|"]
    for r, n, rows_ in con.execute("SELECT reason, COUNT(DISTINCT order_id), COUNT(*) FROM exclusions "
                                   "GROUP BY 1 ORDER BY 2 DESC"):
        md.append(f"| {r} | {n:,} | {rows_:,} |")
    md += ["", "`quiet_hours` and `cap` rows are retried at the next run, so one order can log several. "
           "`dnd` / `no_consent` on a callback downgrade it to a message order update.", "",
           "## Compliance read-back (stored rows, each should be 0)", "",
           "| Check | Count |", "|---|---|",
           f"| Sends outside 09:00-21:00 IST | {out_of_hours} |",
           f"| Holdout orders that received any outreach | {holdout_msgd} |",
           f"| Sends on a channel without consent | {no_consent} |",
           f"| Calls to DND customers | {dnd_calls} |",
           f"| Same order contacted twice within 24h | {twice_24h} |",
           f"| Customer contacted more than {job.WEEKLY_CAP}x in 7 days | {over_cap} |", "",
           "## Monitoring", "",
           "| Signal | This window |", "|---|---|",
           f"| Runs that missed the {job.CUTOFF_MIN}-min cut-off | {sum(1 - r[6] for r in runs)} of {len(runs)} |",
           f"| Morning flag rate > 1.5x its trailing 4-week average | {', '.join(jumps) or 'none'} |",
           f"| UNMAPPED courier codes | {', '.join(f'{c}: {n}' for c, n in unknown) or 'none'} "
           f"(alerted in {sum(r[4] > 0 for r in runs)} runs) |",
           f"| Message delivery failures after one retry | {final_fail} |",
           "| Opt-out / complaint rate | not modelled in synthetic data; weekly check in the runbook |", "",
           "## Data coverage", "",
           f"- Tickets with an order id: **{pct(cover[0])}** (WISMO tickets: {pct(cover[1])}). "
           "Tickets without one are left out of every per-order number above.",
           f"- Courier scans that reached us more than 1h after the event: **{pct(late_scans)}**. "
           "The job reads scans by ingested time and re-reads all open orders every run.",
           "- Timestamps stored in UTC; dates, weeks, quiet hours and run times in IST.", "",
           "## Assumptions", ""]
    md += [f"- `{k}` = {v}" for k, v in ASSUME.items()]
    md += [f"- Rule knobs live at the top of `job.py` (flag at {job.FLAG_AT}, scan gap {job.GAP_H}h, "
           f"lane late rate {job.LANE_LATE:.0%}, weekly cap {job.WEEKLY_CAP}).", f"- {job.TEMPLATE_NOTES}"]
    (HERE / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"wrote {HERE / 'report.md'}")  # not the table itself: Windows consoles choke on the ₹ sign


if __name__ == "__main__":
    main()
