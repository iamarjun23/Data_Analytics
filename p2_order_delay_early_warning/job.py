"""Risk flag + daily CX work list with the compliance filter built in (spec sections 5-9).

run(con, run_ts_utc) sees only what was visible at run time: scans by *ingested* time, deliveries already
scanned. It re-reads every open order on every run, so late courier edits are always picked up (spec 11).
"""
import datetime as dt
import hashlib
import logging
import math
import statistics
import time
from collections import Counter, defaultdict

IST = dt.timedelta(hours=5, minutes=30)
FMT = "%Y-%m-%d %H:%M:%S"
log = logging.getLogger("p2")

# ---- Rule knobs. Edit and rerun; every number here is a placeholder, not a production value.
W = dict(lane_prior=.25, scan_gap=.30, packing_age=.30, stock=.50, address=.50, promise=.20)  # signal weights
FLAG_AT = .40                       # risk_score >= this -> flagged (lane alone is not enough)
LANE_LATE = .25                     # 28-day lane+courier late rate
GAP_H = 36                          # hours since the last courier scan
PROMISE_DAYS = 2                    # promised date within N days and not out for delivery
PACK_SLA_H = {"Dog food": 24, "Cat food": 24, "Pharmacy": 12, "Toys": 24, "Grooming": 24, "Accessories": 36}
HIGH_RISK, HIGH_VALUE = .70, 2200   # -> agent callback
EDD_BUFFER_D = 1                    # new EDD = lane median transit + this
QUIET_IST = (9, 21)                 # no contact outside 09:00-21:00 IST (confirm with compliance)
SEND_DELAY_H = 1                    # list is worked from run time + 1h
MSG_WINDOW_H, CALL_WINDOW_H = .5, 2  # a message goes out within 30 min, a callback within 2h
WEEKLY_CAP = 2                      # proactive contacts per customer per 7 days (spec leaves blank)
CUTOFF_MIN = 30                     # list must be ready 30 min after the run starts

TERMINAL = ("DELIVERED", "RTO", "CANCELLED")
KNOWN = {"PLACED", "PACKED", "SHIPPED", "IN_TRANSIT", "OUT_FOR_DELIVERY", "OOS", *TERMINAL}
MSG_CHANNELS = ("whatsapp", "sms", "email")
TEMPLATE_OF = {"delay_notice": "T_DELAY", "oos_offer": "T_OOS", "address_confirm": "T_ADDR", "callback": "T_CALL"}
# Order updates only: no promo, no product, medicine or pet-health detail (spec 8).
TEMPLATES = {
    "T_DELAY": "Hi {first_name}, your order {order_id} is running late. New expected date: {new_edd}. "
               "No action needed; we will keep you posted.",
    "T_OOS": "Hi {first_name}, an item in order {order_id} went out of stock after you ordered. "
             "Reply 1 to wait, 2 for a substitute, 3 for a full refund.",
    "T_ADDR": "Hi {first_name}, please confirm the delivery address for order {order_id} so we can dispatch it: {link}",
    "T_CALL": "Agent script: apologise for the delay on order {order_id}, give new date {new_edd}, "
              "offer refund of shipping if asked.",
}
TEMPLATE_NOTES = "New EDD = lane's 28-day median actual transit (order date to delivery, IST days) + 1 day buffer."

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs(run_id INTEGER PRIMARY KEY, run_ts_utc TEXT, list_date TEXT, open_orders INT,
  flagged INT, listed INT, excluded INT, unmapped INT, runtime_s REAL, ready_by_cutoff INT);
CREATE TABLE IF NOT EXISTS risk_flags(run_id INT, order_id INT, run_date TEXT, risk_score REAL, reasons TEXT,
  list_date TEXT);
CREATE TABLE IF NOT EXISTS outreach_list(run_id INT, list_date TEXT, order_id INT, first_name TEXT, lane TEXT,
  reasons TEXT, action TEXT, template_id TEXT, channel TEXT, send_at_utc TEXT, call_by_ist TEXT, new_edd TEXT,
  is_holdout INT);
CREATE TABLE IF NOT EXISTS exclusions(run_id INT, order_id INT, run_date TEXT, action TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS outreach_log(order_id INT, sent_at_utc TEXT, channel TEXT, template_id TEXT, status TEXT);
"""

# Lane prior + transit, from deliveries scanned in the last 28 days (sqlite version of the spec's DuckDB query).
LANE_SQL = """
SELECT o.lane, o.courier,
       date(e.event_ts_utc, '+330 minutes') > o.promised_date AS late,
       julianday(date(e.event_ts_utc, '+330 minutes')) - julianday(date(o.order_ts_utc, '+330 minutes')) AS transit_d
FROM shipment_events e JOIN orders o USING (order_id)
WHERE e.status_code = 'DELIVERED' AND e.ingested_ts_utc <= :now AND e.event_ts_utc >= datetime(:now, '-28 days')"""

# Open orders as of :now. Only order-time fields from `orders`; outcome fields are never read here.
OPEN_SQL = """
SELECT o.order_id, o.account_id, o.order_ts_utc, o.category, o.value, o.lane, o.courier, o.promised_date,
       o.address_ok, MAX(e.event_ts_utc), GROUP_CONCAT(e.status_code)
FROM orders o JOIN shipment_events e ON e.order_id = o.order_id AND e.ingested_ts_utc <= :now
WHERE o.order_ts_utc <= :now AND o.order_ts_utc >= datetime(:now, '-30 days')
GROUP BY o.order_id
HAVING SUM(e.status_code IN ('DELIVERED', 'RTO', 'CANCELLED')) = 0"""


def parse(ts):
    return dt.datetime.strptime(ts, FMT)


def is_holdout(order_id):
    """10% of flagged orders get no proactive contact. md5, not hash(): stable across runs and machines."""
    return int(hashlib.md5(str(order_id).encode()).hexdigest(), 16) % 10 == 0


def signals(o, now):
    """Which signals fire for one open order. o: lane_rate, codes, last_scan, order_ts, category,
    address_ok, promised (date)."""
    shipped = "SHIPPED" in o["codes"]
    fired = {
        "lane_prior": o["lane_rate"] > LANE_LATE,
        "scan_gap": shipped and now - o["last_scan"] > dt.timedelta(hours=GAP_H),
        "packing_age": not shipped and now - o["order_ts"] > dt.timedelta(hours=PACK_SLA_H[o["category"]]),
        "stock": "OOS" in o["codes"],
        "address": not o["address_ok"],
        "promise": (o["promised"] - (now + IST).date()).days <= PROMISE_DAYS
                   and "OUT_FOR_DELIVERY" not in o["codes"],
    }
    return [k for k, v in fired.items() if v]


def score(reasons):
    return round(min(1.0, sum(W[r] for r in reasons)), 2)


def choose_action(reasons, risk, value):
    if "stock" in reasons:
        return "oos_offer"
    if "address" in reasons:
        return "address_confirm"
    if risk >= HIGH_RISK and value >= HIGH_VALUE:
        return "callback"
    return "delay_notice"


def downgrade(action, granted, dnd):
    """A callback is not a strictly transactional update: DND or no call consent -> message instead.
    Message order updates are transactional, so DND does not block them."""
    if action == "callback" and (dnd or "call" not in granted):
        return "delay_notice", [("callback", "dnd" if dnd else "no_consent")]
    return action, []


def quiet_ok(start, hours):
    s, e = start + IST, start + IST + dt.timedelta(hours=hours)
    return s.date() == e.date() and dt.time(QUIET_IST[0]) <= s.time() and e.time() <= dt.time(QUIET_IST[1])


def route(action, pref, granted, send_at, capped):
    """Compliance filter for one row. Returns (channel, None) or (None, exclusion reason)."""
    if capped:
        return None, "cap"
    if action == "callback":
        return ("call", None) if quiet_ok(send_at, CALL_WINDOW_H) else (None, "quiet_hours")
    if not quiet_ok(send_at, MSG_WINDOW_H):
        return None, "quiet_hours"
    ch = pref if pref in granted else next((c for c in MSG_CHANNELS if c in granted), None)
    return (ch, None) if ch else (None, "no_consent")


def new_edd(order_ts, median_transit, now, promised):
    order_day = (parse(order_ts) + IST).date()
    base = order_day + dt.timedelta(days=median_transit) if median_transit is not None \
        else dt.date.fromisoformat(promised)
    return max(base + dt.timedelta(days=EDD_BUFFER_D), (now + IST).date() + dt.timedelta(days=1)).isoformat()


def lane_stats(con, nows):
    by = defaultdict(list)
    for lane, courier, late, transit in con.execute(LANE_SQL, {"now": nows}):
        by[(lane, courier)].append((late, transit))
    return {k: {"late": sum(x for x, _ in v) / len(v), "median": math.ceil(statistics.median(t for _, t in v))}
            for k, v in by.items()}


def run(con, now):
    """One flag + list run at `now` (naive UTC datetime). Writes risk_flags, outreach_list, exclusions, job_runs."""
    t0 = time.perf_counter()
    nows, today = now.strftime(FMT), (now + IST).date().isoformat()
    send_at = now + dt.timedelta(hours=SEND_DELAY_H)
    sends = send_at.strftime(FMT)
    run_id = con.execute("INSERT INTO job_runs(run_ts_utc, list_date) VALUES (?, ?)", (nows, today)).lastrowid

    lanes = lane_stats(con, nows)
    acc = {a: (n, p) for a, n, p in con.execute("SELECT account_id, first_name, preferred_channel FROM accounts")}
    granted, dnd = defaultdict(set), {}
    for a, ch, st, d in con.execute("SELECT customer_id, channel, consent_status, dnd_flag FROM consent"):
        dnd[a] = d
        if st == "granted":
            granted[a].add(ch)
    # Already decided: listed (incl. holdout) or permanently excluded. Quiet hours and cap are retried next run.
    done = set(con.execute("SELECT order_id, action FROM outreach_list UNION "
                           "SELECT order_id, action FROM exclusions WHERE reason NOT IN ('quiet_hours', 'cap')"))
    last = dict(con.execute("SELECT order_id, MAX(send_at_utc) FROM outreach_list "
                            "WHERE channel IS NOT NULL GROUP BY 1"))
    week = Counter(dict(con.execute(
        "SELECT o.account_id, COUNT(*) FROM outreach_list l JOIN orders o USING (order_id) "
        "WHERE l.channel IS NOT NULL AND l.send_at_utc > ? GROUP BY 1",
        ((send_at - dt.timedelta(days=7)).strftime(FMT),))))
    day_ago = (send_at - dt.timedelta(hours=24)).strftime(FMT)

    flags, rows, excl, unmapped, n_open = [], [], [], 0, 0
    for oid, acc_id, ots, cat, value, lane, courier, promised, addr_ok, last_scan, codes in \
            con.execute(OPEN_SQL, {"now": nows}).fetchall():
        n_open += 1
        codes = set(codes.split(","))
        unmapped += len(codes - KNOWN)  # unknown courier codes: counted, alerted, still treated as a scan
        ls = lanes.get((lane, courier), {})
        reasons = signals(dict(lane_rate=ls.get("late", 0.0), codes=codes, last_scan=parse(last_scan),
                               order_ts=parse(ots), category=cat, address_ok=addr_ok,
                               promised=dt.date.fromisoformat(promised)), now)
        risk = score(reasons)
        if risk < FLAG_AT:
            continue
        why = ",".join(reasons)
        flags.append((run_id, oid, today, risk, why, today))
        action, pre = downgrade(choose_action(reasons, risk, value), granted[acc_id], dnd.get(acc_id, 1))
        if (oid, action) in done:
            continue
        name, pref = acc[acc_id]
        base = [run_id, today, oid, name, lane, why, action, TEMPLATE_OF[action]]
        edd = new_edd(ots, ls.get("median"), now, promised)
        if is_holdout(oid):
            rows.append(base + [None, None, None, edd, 1])
            excl.append((run_id, oid, today, action, "holdout"))
            continue
        excl += [(run_id, oid, today, a, r) for a, r in pre]
        capped = last.get(oid, "") > day_ago or week[acc_id] >= WEEKLY_CAP
        ch, reason = route(action, pref, granted[acc_id], send_at, capped)
        if reason:
            excl.append((run_id, oid, today, action, reason))
            continue
        call_by = (send_at + IST + dt.timedelta(hours=CALL_WINDOW_H)).strftime("%H:%M") if ch == "call" else None
        rows.append(base + [ch, sends, call_by, edd, 0])
        last[oid] = sends
        week[acc_id] += 1

    con.executemany("INSERT INTO risk_flags VALUES (?,?,?,?,?,?)", flags)
    con.executemany("INSERT INTO outreach_list VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO exclusions VALUES (?,?,?,?,?)", excl)
    rt = time.perf_counter() - t0
    rec = dict(run_id=run_id, open_orders=n_open, flagged=len(flags), listed=sum(1 for r in rows if not r[-1]),
               excluded=len(excl), unmapped=unmapped, runtime_s=round(rt, 3), ready_by_cutoff=int(rt < CUTOFF_MIN * 60))
    con.execute("UPDATE job_runs SET open_orders=:open_orders, flagged=:flagged, listed=:listed, excluded=:excluded, "
                "unmapped=:unmapped, runtime_s=:runtime_s, ready_by_cutoff=:ready_by_cutoff WHERE run_id=:run_id", rec)
    con.commit()
    if unmapped:
        log.warning("run %s: %s UNMAPPED courier status codes in open orders", run_id, unmapped)
    if not rec["ready_by_cutoff"]:
        log.error("run %s missed the cut-off; fall back to yesterday's list", run_id)
    return rec
