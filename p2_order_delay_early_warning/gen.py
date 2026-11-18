"""Synthetic world for project 2: accounts, consent, orders, courier scans. SYNTHETIC, no real data.

Reuses the shared lanes and categories from common/gen_data.py, at timestamp grain, with planted patterns:
  - BLR-DEL / BLR-KOL / BLR-NE on CourierX are late far more often
  - late orders mostly show a 2-4 day scan gap (stuck at a hub) or slow packing; some show nothing
  - Dog food goes out of stock after order more than other categories
  - ~7% of addresses fail validation; half of those end in RTO, the rest arrive late
  - ~5% of courier scans reach us 12-60h late (backfills), ~0.5% carry an unknown status code
Tickets are NOT made here: they depend on whether we messaged first, so simulate.py makes them after the job runs.
"""
import datetime as dt
import pathlib
import random
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from common.gen_data import BAD_ADDR, CATS, LANES  # noqa: E402
from job import FMT, IST, PACK_SLA_H  # noqa: E402

START, END = dt.date(2026, 9, 1), dt.date(2026, 10, 31)
NAMES = ["Asha", "Ravi", "Meera", "Karan", "Divya", "Arun", "Neha", "Vikram", "Pooja", "Sameer", "Lakshmi", "Rahul"]
CONSENT_P = {"whatsapp": .85, "sms": .90, "email": .70, "call": .60}
DND_P, LAG_P, UNKNOWN_P = .12, .05, .005

SCHEMA = """
CREATE TABLE accounts(account_id INT PRIMARY KEY, first_name TEXT, preferred_channel TEXT);
CREATE TABLE consent(customer_id INT, channel TEXT, consent_status TEXT, dnd_flag INT);
CREATE TABLE orders(order_id INT PRIMARY KEY, account_id INT, order_ts_utc TEXT, category TEXT, value INT, lane TEXT,
  courier TEXT, promised_date TEXT, delivered_ts_utc TEXT, status TEXT, address_ok INT, stock_status TEXT);
CREATE TABLE shipment_events(order_id INT, event_ts_utc TEXT, status_code TEXT, location TEXT, ingested_ts_utc TEXT);
CREATE INDEX ev_order ON shipment_events(order_id);
CREATE INDEX ev_status ON shipment_events(status_code, event_ts_utc);
CREATE INDEX ord_ts ON orders(order_ts_utc);
"""


def h(x):
    return dt.timedelta(hours=x)


def make_order(oid, acc, day, rnd, ev):
    lane = rnd.choices(list(LANES), [v[3] for v in LANES.values()])[0]
    transit, p_late, courier, _ = LANES[lane]
    cat = rnd.choices(list(CATS), [v[3] for v in CATS.values()])[0]
    price, _, p_oos, _ = CATS[cat]
    addr_ok = rnd.random() > BAD_ADDR
    placed = dt.datetime.combine(day, dt.time(9)) - IST + dt.timedelta(minutes=rnd.randint(0, 14 * 60))
    promised = day + dt.timedelta(days=transit + 1)
    row = dict(order_id=oid, account_id=acc, order_ts_utc=placed.strftime(FMT), category=cat,
               value=max(150, int(rnd.gauss(price, price * .25))), lane=lane, courier=courier,
               promised_date=promised.isoformat(), delivered_ts_utc=None, status="delivered",
               address_ok=int(addr_ok), stock_status="ok")

    def scan(t, code, courier_scan=True):
        lag = rnd.uniform(12, 60) if courier_scan and rnd.random() < LAG_P else 0
        if code == "IN_TRANSIT" and rnd.random() < UNKNOWN_P:
            code = "HUB_HOLD_X"
        ev.append((oid, t.strftime(FMT), code, lane[4:] if courier_scan else "BLR", (t + h(lag)).strftime(FMT)))

    scan(placed, "PLACED", False)
    if rnd.random() < p_oos:
        scan(placed + h(rnd.uniform(3, 12)), "OOS", False)
        scan(dt.datetime.combine(promised, dt.time(12)) - IST, "CANCELLED", False)
        row.update(status="cancelled_oos", stock_status="oos_after_order")
        return row

    late = rnd.random() < p_late or not addr_ok
    cause = rnd.choices(["stuck", "packing", "silent"], [.65, .25, .10])[0] if late else None
    sla = PACK_SLA_H[cat]
    t = placed + h(sla * (rnd.uniform(1.2, 2.5) if cause == "packing" else rnd.uniform(.1, .6)))
    scan(t, "PACKED")
    t += h(rnd.uniform(2, 8))
    scan(t, "SHIPPED")
    arrive = promised + dt.timedelta(days=rnd.randint(1, 5)) if late else promised - dt.timedelta(days=rnd.randint(0, 1))
    end = max(dt.datetime.combine(arrive, dt.time(10)) - IST + h(rnd.uniform(0, 9)), t + h(8))
    ofd = end - h(rnd.uniform(3, 6))
    hops, c = [], t + h(rnd.uniform(8, 20))
    while c < ofd:
        hops.append(c)
        c += h(rnd.uniform(8, 20))
    if cause == "stuck":  # planted: one 2-4 day hole in the scans
        g0 = t + (ofd - t) * rnd.uniform(.05, .4)
        g1 = g0 + h(rnd.uniform(48, 96))
        hops = [x for x in hops if not g0 < x < g1]
    for x in hops:
        scan(x, "IN_TRANSIT")
    scan(ofd, "OUT_FOR_DELIVERY")
    if not addr_ok and rnd.random() < .5:
        scan(end, "RTO")
        row["status"] = "rto"
    else:
        scan(end, "DELIVERED")
        row["delivered_ts_utc"] = end.strftime(FMT)
    return row


def world(path, start=START, end=END, per_day=250, seed=11):
    """Build the synthetic world into a fresh sqlite file; returns the open connection."""
    rnd = random.Random(seed)
    n_acc = per_day * 20
    accounts = [(a, rnd.choice(NAMES), rnd.choice(("whatsapp", "whatsapp", "sms", "email")))
                for a in range(1, n_acc + 1)]
    consent = []
    for a, *_ in accounts:
        dnd = int(rnd.random() < DND_P)
        consent += [(a, ch, "granted" if rnd.random() < p else "denied", dnd) for ch, p in CONSENT_P.items()]
    orders, ev = [], []
    for d in range((end - start).days + 1):
        for _ in range(per_day):
            orders.append(make_order(len(orders) + 1, rnd.randint(1, n_acc), start + dt.timedelta(days=d), rnd, ev))

    path = pathlib.Path(path)
    path.parent.mkdir(exist_ok=True)
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO accounts VALUES (?,?,?)", accounts)
    con.executemany("INSERT INTO consent VALUES (?,?,?,?)", consent)
    con.executemany("INSERT INTO orders VALUES (:order_id,:account_id,:order_ts_utc,:category,:value,:lane,:courier,"
                    ":promised_date,:delivered_ts_utc,:status,:address_ok,:stock_status)", orders)
    con.executemany("INSERT INTO shipment_events VALUES (?,?,?,?,?)", ev)
    con.commit()
    return con


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parent.parent / "data" / "p2.db"
    c = world(out)
    print(f"orders={c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]} "
          f"scans={c.execute('SELECT COUNT(*) FROM shipment_events').fetchone()[0]} -> {out}")
