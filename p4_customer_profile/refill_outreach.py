"""First consumer of the profile: overdue refill / reorder call list, compliance filter, holdout, lift.
Run: py p4_customer_profile/refill_outreach.py --purpose "weekly refill reminders"
  -> outreach tables in data/p4.db, data/outreach_list_<run>.csv, outreach_report.md

Overdue: refill_due  = today > last_fill_date + refill_days + 3 and no pharmacy order since.
         reorder_due = no chronic medication and days since last order > 1.5 x the customer's usual gap.
Every exclusion is logged with its reason. 10% holdout: hash(customer_id) % 10 == 0.
The list carries a reason code only, never a medicine, diagnosis or phone number.
"""
import argparse
import collections
import csv
import datetime as dt
import getpass
import hashlib
import math
import pathlib
import random
import sqlite3

from build_profile import norm_phone
from gen_identity_data import AS_OF, DB

HERE = pathlib.Path(__file__).resolve().parent
ASSUME = dict(channel="call", hours=(9, 21), cap_days=7, grace_days=3, gap_mult=1.5, holdout_mod=10,
              script_id="SCR_REFILL_V1", exclusion_jump_pts=10,
              margin=0.25, agent_minutes=4, cost_per_min=6)  # Rs; placeholders, edit and rerun
# SYNTHETIC outcome model, stands in for 14 days of real orders. Replace simulate_outcomes() in production.
SIM = dict(seed=11, base=0.30, uplift=0.08, optout_contact=0.012, optout_holdout=0.002)

OVERDUE = """
SELECT DISTINCT x.customer_id, 'refill_due'
FROM pharmacy_rx r JOIN customer_xwalk x ON x.source = 'pharmacy_rx' AND x.source_id = r.rx_id
WHERE julianday(:as_of) - julianday(r.last_fill_date, '+' || r.refill_days || ' day') > :grace
  AND NOT EXISTS (SELECT 1 FROM customer_xwalk xo JOIN orders o ON xo.source = 'orders' AND o.order_id = xo.source_id
                  WHERE xo.customer_id = x.customer_id AND o.category = 'pharmacy' AND o.order_date > r.last_fill_date)
UNION ALL
SELECT customer_id, 'reorder_due' FROM customer_360
WHERE NOT has_chronic_medication AND usual_gap_days IS NOT NULL
  AND julianday(:as_of) - julianday(last_order_date) > :mult * usual_gap_days
"""

TABLES = """
CREATE TABLE IF NOT EXISTS outreach_runs(run_id TEXT, run_by TEXT, run_at TEXT, purpose TEXT,
    candidates INT, excluded INT, holdout INT, contacted INT);
CREATE TABLE IF NOT EXISTS exclusions(run_id TEXT, customer_id TEXT, reason_code TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS outreach_assignments(run_id TEXT, customer_id TEXT, reason_code TEXT, arm TEXT);
CREATE TABLE IF NOT EXISTS outreach_list(run_id TEXT, customer_id TEXT, first_name TEXT, reason_code TEXT,
    preferred_channel TEXT, call_by TEXT, script_id TEXT);
CREATE TABLE IF NOT EXISTS outcomes_14d(run_id TEXT, customer_id TEXT, reordered INT, revenue INT, opted_out INT);
"""


def in_holdout(cid):
    return int(hashlib.sha256(cid.encode()).hexdigest(), 16) % ASSUME["holdout_mod"] == 0


def run(con, purpose, run_by, now=None):
    # ponytail: clock pinned to the data snapshot for reproducible runs; production passes datetime.now(IST)
    now = now or dt.datetime.combine(AS_OF, dt.time(10, 0))
    ch, (_, close) = ASSUME["channel"], ASSUME["hours"]
    call_day = now.date() + dt.timedelta(days=now.hour >= close)  # too late to call today -> tomorrow 09:00-21:00
    call_by, run_id = f"{call_day} {close}:00 IST", f"R{call_day:%Y%m%d}"

    cands = dict(con.execute(OVERDUE, dict(as_of=str(AS_OF), grace=ASSUME["grace_days"], mult=ASSUME["gap_mult"])))
    prof = {r[0]: r[1:] for r in con.execute(f"SELECT customer_id, dnd_flag, consent_{ch}, match_quality FROM customer_360")}
    phones, names = collections.defaultdict(set), {}
    for cid, p, primary, fn in con.execute("SELECT customer_id, phone_norm, is_primary, first_name FROM contact_lookup"):
        phones[cid].add(p)
        if primary:
            names[cid] = fn
    cutoff = str(AS_OF - dt.timedelta(days=ASSUME["cap_days"]))
    recent = {norm_phone(p) for p, ts in con.execute("SELECT phone, contacted_at FROM contact_log") if ts[:10] > cutoff}

    excl, assign = [], []
    for cid in sorted(cands):
        dnd, consent, quality = prof[cid]
        # ponytail: DND excludes refill reminders too, until compliance rules on transactional reminders
        why = ("dnd" if dnd else
               "no_consent" if consent != "granted" else
               "contact_cap" if phones[cid] & recent else
               "match_review" if quality == "C" else None)
        if why:
            excl.append((run_id, cid, cands[cid], why))
        else:
            assign.append((run_id, cid, cands[cid], "holdout" if in_holdout(cid) else "contact"))
    lst = [(run_id, cid, names.get(cid), code, ch, call_by, ASSUME["script_id"])
           for _, cid, code, arm in assign if arm == "contact"]

    alert = None
    prev = con.execute("SELECT excluded * 100.0 / candidates FROM outreach_runs WHERE run_id < ? "
                       "ORDER BY run_id DESC LIMIT 1", (run_id,)).fetchone() if has_table(con, "outreach_runs") else None
    share = 100.0 * len(excl) / max(len(cands), 1)
    if prev and share - prev[0] > ASSUME["exclusion_jump_pts"]:
        alert = f"WARN: exclusion share {prev[0]:.0f}% -> {share:.0f}%; check the consent feed before sending"

    con.isolation_level = None
    con.executescript(TABLES)
    con.execute("BEGIN")
    for t in ("outreach_runs", "exclusions", "outreach_assignments", "outreach_list", "outcomes_14d"):
        con.execute(f"DELETE FROM {t} WHERE run_id = ?", (run_id,))  # rerun of the same day replaces it
    con.execute("INSERT INTO outreach_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, run_by, now.isoformat(), purpose, len(cands), len(excl), len(assign) - len(lst), len(lst)))
    con.executemany("INSERT INTO exclusions VALUES (?, ?, ?, ?)", excl)
    con.executemany("INSERT INTO outreach_assignments VALUES (?, ?, ?, ?)", assign)
    con.executemany("INSERT INTO outreach_list VALUES (?, ?, ?, ?, ?, ?, ?)", lst)
    # Read back: what is stored must equal what the filter produced, and nobody blocked may be on it.
    stored = sorted(con.execute("SELECT * FROM outreach_list WHERE run_id = ?", (run_id,)).fetchall())
    leaked = con.execute("""SELECT COUNT(*) FROM outreach_list l JOIN customer_360 c USING (customer_id)
                            WHERE l.run_id = ? AND (c.dnd_flag OR c.consent_call != 'granted' OR c.match_quality = 'C')""",
                         (run_id,)).fetchone()[0]
    if stored != sorted(lst) or leaked:
        con.execute("ROLLBACK")
        raise SystemExit(f"Read-back failed for {run_id} (stored {len(stored)}, expected {len(lst)}, blocked {leaked}); nothing sent")
    con.execute("COMMIT")
    return dict(run_id=run_id, candidates=len(cands), excluded=len(excl), list=lst, alert=alert)


def has_table(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None


def simulate_outcomes(con, run_id):
    """SYNTHETIC. Production: reordered = any order by the customer within 14 days of call_by."""
    r = random.Random(SIM["seed"])
    aov = dict(con.execute("SELECT customer_id, COALESCE(aov, 800) FROM customer_360"))
    rows = []
    for cid, arm in con.execute("SELECT customer_id, arm FROM outreach_assignments WHERE run_id = ? ORDER BY 1", (run_id,)).fetchall():
        c = arm == "contact"
        reordered = r.random() < SIM["base"] + SIM["uplift"] * c
        rows.append((run_id, cid, int(reordered), round(aov[cid] * r.uniform(0.8, 1.2)) if reordered else 0,
                     int(r.random() < SIM["optout_contact" if c else "optout_holdout"])))
    con.executemany("INSERT INTO outcomes_14d VALUES (?, ?, ?, ?, ?)", rows)


def lift_report(con, res):
    run_id = res["run_id"]
    arms = {arm: dict(n=n, re=re, rev=rev, opt=opt) for arm, n, re, rev, opt in con.execute("""
        SELECT a.arm, COUNT(*), SUM(o.reordered), SUM(o.revenue), SUM(o.opted_out)
        FROM outreach_assignments a JOIN outcomes_14d o USING (run_id, customer_id)
        WHERE a.run_id = ? GROUP BY 1""", (run_id,))}
    c, h = arms["contact"], arms["holdout"]
    pc, ph = c["re"] / c["n"], h["re"] / h["n"]
    lift, se = pc - ph, math.sqrt(pc * (1 - pc) / c["n"] + ph * (1 - ph) / h["n"])
    rev_per_order = c["rev"] / max(c["re"], 1)
    minutes = c["n"] * ASSUME["agent_minutes"]
    cost = minutes * ASSUME["cost_per_min"]
    extra_orders = lift * c["n"]
    net = extra_orders * rev_per_order * ASSUME["margin"] - cost
    margin = lambda a: round(a["rev"] * ASSUME["margin"])
    reasons = con.execute("SELECT reason, COUNT(*) FROM exclusions WHERE run_id = ? GROUP BY 1 ORDER BY 2 DESC", (run_id,)).fetchall()
    codes = con.execute("SELECT reason_code, arm, COUNT(*) FROM outreach_assignments WHERE run_id = ? GROUP BY 1, 2 ORDER BY 1, 2", (run_id,)).fetchall()
    rs = lambda x: f"Rs {x:,.0f}"
    out = [f"# Overdue-refill outreach, run {run_id}", "",
           "SYNTHETIC data. **Outcomes are simulated** (planted uplift "
           f"{SIM['uplift']:.0%} on a {SIM['base']:.0%} base) until real 14-day orders replace `simulate_outcomes()`.", "",
           "## Funnel", "", "| Step | Customers |", "| --- | ---: |",
           f"| Overdue (refill_due + reorder_due) | {res['candidates']:,} |"]
    out += [f"| excluded: {why} | {n:,} |" for why, n in reasons]
    out += [f"| Eligible | {c['n'] + h['n']:,} |", f"| Holdout (10%) | {h['n']:,} |", f"| On the call list | {c['n']:,} |", "",
            "| Reason code | Arm | Customers |", "| --- | --- | ---: |"]
    out += [f"| {code} | {arm} | {n:,} |" for code, arm, n in codes]
    out += ["", "## Lift (reorder within 14 days)", "",
            "| Item | Contacted | Holdout | Difference |", "| --- | ---: | ---: | ---: |",
            f"| Eligible customers | {c['n']:,} | {h['n']:,} | |",
            f"| Reordered within 14 days | {c['re']:,} ({pc:.1%}) | {h['re']:,} ({ph:.1%}) | {lift * 100:+.1f} pts |",
            f"| Revenue | {rs(c['rev'])} | {rs(h['rev'])} | |",
            f"| Margin at {ASSUME['margin']:.0%} | {rs(margin(c))} | {rs(margin(h))} | |",
            f"| Opt-outs or complaints | {c['opt']} ({c['opt'] / c['n']:.1%}) | {h['opt']} ({h['opt'] / h['n']:.1%}) | |",
            f"| Agent minutes and cost | {minutes:,} min, {rs(cost)} | 0 | |", "",
            f"- Lift: **{lift * 100:+.1f} pts**, 95% interval {(lift - 1.96 * se) * 100:+.1f} to {(lift + 1.96 * se) * 100:+.1f} pts"
            + (" (does not exclude zero: holdout too small to call it)" if abs(lift) < 1.96 * se else "") + ".",
            f"- Extra orders: {extra_orders:,.0f} x {rs(rev_per_order)} x {ASSUME['margin']:.0%} margin - {rs(cost)} agent cost "
            f"= **net {rs(net)}** for this run.",
            f"- Opt-out rate on contacted: {c['opt'] / c['n']:.1%}. Report it next to the lift, every run.", "",
            f"List columns sent to the team: customer_id (hashed), first_name, reason_code, preferred_channel, call_by, script_id."]
    if res["alert"]:
        out += ["", f"**{res['alert']}**"]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--purpose", required=True, help="why this list is being run; stored in outreach_runs")
    ap.add_argument("--run-by", default=getpass.getuser())
    a = ap.parse_args()
    con = sqlite3.connect(DB)
    res = run(con, a.purpose, a.run_by)
    if res["alert"]:
        print(res["alert"])
    path = DB.parent / f"outreach_list_{res['run_id']}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "first_name", "reason_code", "preferred_channel", "call_by", "script_id"])
        w.writerows(row[1:] for row in res["list"])
    simulate_outcomes(con, res["run_id"])
    (HERE / "outreach_report.md").write_text(lift_report(con, res), encoding="utf-8")
    print((HERE / "outreach_report.md").read_text(encoding="utf-8"))
    print(f"-> {path}")
