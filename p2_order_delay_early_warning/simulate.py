"""End-to-end run on synthetic data:
build world -> run the job at 08:00 and 19:30 IST every day -> 'send' the list -> realise outcomes -> report.

The treatment effect below is PLANTED so the report has something to find. The report never reads EFFECT:
it has to recover the effect from the 10% holdout, the same way it would on real data.
Run: py p2_order_delay_early_warning/simulate.py   ->  data/p2.db, p2_order_delay_early_warning/report.md
"""
import datetime as dt
import logging
import pathlib
import random
import time

import gen
import job
import report
from job import FMT, IST, parse

DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "p2.db"
RUNS_FROM = dt.date(2026, 9, 29)  # the first 28 days only build the lane prior
RUN_TIMES_IST = (dt.time(8, 0), dt.time(19, 30))
CHANNELS = ("chat", "call", "email", "whatsapp")

EFFECT = dict(  # P(event); "_reached" = we got a message or call through before the promised day ended
    wismo_late=.60, wismo_late_reached=.30,
    wismo_ontime=.03, wismo_ontime_reached=.06,  # a heads-up on an order that turns up on time creates contacts
    oos=.50, oos_reached=.15, oos_kept_reached=.40,  # reached OOS customers sometimes take a substitute
    addr=.70, addr_reached=.35,
    credit_late=.18, credit_late_reached=.08,
    other=.03, no_order_id=.04, csat_response=.5)
CSAT_W = {"bad": [.30, .25, .25, .12, .08], "bad_reached": [.10, .15, .30, .25, .20], "ok": [.05, .10, .25, .30, .30]}


def h(x):
    return dt.timedelta(hours=x)


def send(con, run_id, rnd):
    """Stand-in for the messaging system: sends each listed row inside its window, retries a failed message once."""
    out = []
    for oid, ch, tpl, at in con.execute("SELECT order_id, channel, template_id, send_at_utc FROM outreach_list "
                                        "WHERE run_id = ? AND channel IS NOT NULL", (run_id,)).fetchall():
        if ch == "call":
            t = parse(at) + dt.timedelta(minutes=rnd.randint(0, 110))
            out.append((oid, t.strftime(FMT), ch, tpl, "connected" if rnd.random() < .7 else "no_answer"))
            continue
        t = parse(at) + dt.timedelta(minutes=rnd.randint(0, 25))
        ok = rnd.random() < .97
        out.append((oid, t.strftime(FMT), ch, tpl, "delivered" if ok else "failed"))
        if not ok:
            out.append((oid, (t + dt.timedelta(minutes=5)).strftime(FMT), ch, tpl,
                        "delivered" if rnd.random() < .9 else "failed"))
    con.executemany("INSERT INTO outreach_log VALUES (?,?,?,?,?)", out)


def outcomes(con, rnd):
    """Tickets, refunds and CSAT, given what actually happened to each order and whether we reached the customer."""
    E = EFFECT
    reached = dict(con.execute("SELECT order_id, MIN(sent_at_utc) FROM outreach_log "
                               "WHERE status IN ('delivered', 'connected') GROUP BY 1"))
    con.executescript("""
      DROP TABLE IF EXISTS tickets; DROP TABLE IF EXISTS refunds;
      CREATE TABLE tickets(ticket_id INTEGER PRIMARY KEY, order_id INT, account_id INT, created_at_utc TEXT,
                           reason TEXT, channel TEXT, csat INT);
      CREATE TABLE refunds(order_id INT, amount INT, reason TEXT, created_at_utc TEXT);""")
    tickets, refunds = [], []
    for oid, acc, value, promised, delivered, status in con.execute(
            "SELECT order_id, account_id, value, promised_date, delivered_ts_utc, status FROM orders").fetchall():
        pday = dt.date.fromisoformat(promised)
        p0 = dt.datetime.combine(pday, dt.time(10)) - IST  # 10:00 IST on the promised day
        hit = reached.get(oid, "9") < (p0 + h(14)).strftime(FMT)
        late = status == "delivered" and (parse(delivered) + IST).date() > pday
        w = CSAT_W["ok" if status == "delivered" and not late else "bad_reached" if hit else "bad"]

        def ticket(reason, p, lo, hi):
            if rnd.random() < p:
                tickets.append((None, None if rnd.random() < E["no_order_id"] else oid, acc,
                                (p0 + h(rnd.uniform(lo, hi))).strftime(FMT), reason, rnd.choice(CHANNELS),
                                rnd.choices(range(1, 6), w)[0] if rnd.random() < E["csat_response"] else None))

        if status == "delivered":
            if late:
                ticket("wismo", E["wismo_late_reached" if hit else "wismo_late"], 0, 34)
                if rnd.random() < E["credit_late_reached" if hit else "credit_late"]:
                    refunds.append((oid, int(value * rnd.uniform(.1, .3)), "late_credit", (p0 + h(48)).strftime(FMT)))
            else:
                ticket("wismo", E["wismo_ontime_reached" if hit else "wismo_ontime"], -10, 10)
        elif status == "cancelled_oos":
            ticket("oos", E["oos_reached" if hit else "oos"], -20, 10)
            if not (hit and rnd.random() < E["oos_kept_reached"]):
                refunds.append((oid, value, "oos_cancel", p0.strftime(FMT)))
        else:  # rto
            ticket("wrong_address", E["addr_reached" if hit else "addr"], 0, 48)
            refunds.append((oid, value, "rto", (p0 + h(72)).strftime(FMT)))
        ticket("other", E["other"], -24, 72)
    con.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?)", tickets)
    con.executemany("INSERT INTO refunds VALUES (?,?,?,?)", refunds)


def main():
    logging.basicConfig(level=logging.ERROR)  # UNMAPPED warnings are summarised in the report instead
    t0 = time.perf_counter()
    con = gen.world(DB)
    con.executescript(job.SCHEMA)
    rnd = random.Random(5)
    d = RUNS_FROM
    while d <= gen.END:
        for t in RUN_TIMES_IST:
            rec = job.run(con, dt.datetime.combine(d, t) - IST)
            send(con, rec["run_id"], rnd)
        d += dt.timedelta(days=1)
    outcomes(con, rnd)
    con.commit()
    report.main(con)
    print(f"done in {time.perf_counter() - t0:.0f}s -> {DB}")


if __name__ == "__main__":
    main()
