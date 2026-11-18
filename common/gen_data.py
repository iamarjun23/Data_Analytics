"""Synthetic Supertails-shaped CX data (orders, tickets, refunds, returns).

SYNTHETIC: same structure as an e-commerce/pet-care ops stack, zero real customer data.
Patterns are PLANTED so the analysis has something real to find:
  - lanes BLR-DEL / BLR-KOL / BLR-NE on CourierX are late far more often
  - Dog food goes out of stock after purchase more than other categories
  - ~7% of addresses are bad (half end in RTO)
  - SKU BED-L (Accessories) is returned ~6x more than its category (size issue)
  - late / OOS / RTO orders repeat-purchase less
  - ~4% of tickets have no order_id (calls from unknown numbers)
Run: py common/gen_data.py   ->  data/cx.db
"""
import datetime as dt
import pathlib
import random
import sqlite3

random.seed(7)
DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "cx.db"
START, END = dt.date(2026, 8, 1), dt.date(2026, 10, 31)
N_CUSTOMERS = 12000

# lane: (transit days, P(late), courier, weight)
LANES = {
    "BLR-BLR": (1, .05, "Express", 30), "BLR-HYD": (3, .08, "CourierA", 12),
    "BLR-CHN": (3, .08, "CourierA", 10), "BLR-MUM": (4, .10, "CourierB", 10),
    "BLR-PUN": (4, .10, "CourierB", 6), "BLR-DEL": (5, .45, "CourierX", 12),
    "BLR-KOL": (5, .52, "CourierX", 8), "BLR-NE": (7, .58, "CourierX", 4),
}
# category: (avg price, P(return), P(out of stock after order), weight)
CATS = {
    "Dog food": (1800, .02, .09, 30), "Cat food": (1200, .02, .05, 18),
    "Pharmacy": (650, .01, .02, 20), "Toys": (400, .04, .02, 10),
    "Grooming": (700, .04, .02, 8), "Accessories": (1500, .05, .02, 14),
}
BAD_ADDR = .07
REPEAT_P = {"clean": .56, "late": .34, "oos": .30, "rto": .28}


def pick(d, wi):  # weighted choice over dict keys
    return random.choices(list(d), [v[wi] for v in d.values()])[0]


def make_order(oid, cid, day):
    lane = pick(LANES, 3)
    transit, p_late, courier, _ = LANES[lane]
    cat = pick(CATS, 3)
    price, p_ret, p_oos, _ = CATS[cat]
    sku = "BED-L" if cat == "Accessories" and random.random() < .25 else f"{cat[:3].upper()}-{random.randint(1, 9)}"
    value = max(150, int(random.gauss(price, price * .25)))
    addr_ok = random.random() > BAD_ADDR
    promised = day + dt.timedelta(days=transit + 1)
    status, delivered = "delivered", None
    if random.random() < p_oos:
        status = "cancelled_oos"
    elif not addr_ok and random.random() < .5:
        status = "rto"
    else:
        late = random.random() < p_late or (not addr_ok)
        delivered = promised + dt.timedelta(days=random.randint(1, 5)) if late else \
            promised - dt.timedelta(days=random.randint(0, 1))
    return dict(order_id=oid, customer_id=cid, order_date=day.isoformat(), category=cat, sku=sku,
                value=value, lane=lane, courier=courier, promised_date=promised.isoformat(),
                delivered_date=delivered.isoformat() if delivered else None,
                status=status, address_ok=int(addr_ok))


def main():
    orders, tickets, refunds, returns = [], [], [], []
    oid = tid = rid = rtid = 0
    span = (END - START).days

    def ticket(o, reason, when):
        nonlocal tid
        tid += 1
        order_id = None if random.random() < .04 else o["order_id"]
        tickets.append((tid, order_id, o["customer_id"], when.isoformat(), reason,
                        random.choice(["chat", "call", "email", "whatsapp"])))

    def refund(o, amt, reason, by):
        nonlocal rid
        rid += 1
        refunds.append((rid, o["order_id"], amt, reason, by, o["order_date"]))

    for cid in range(1, N_CUSTOMERS + 1):
        day = START + dt.timedelta(days=random.randint(0, span))
        for _ in range(4):
            if day > END:
                break
            oid += 1
            o = make_order(oid, cid, day)
            orders.append(o)
            late = o["delivered_date"] and o["delivered_date"] > o["promised_date"]
            ref_day = dt.date.fromisoformat(o["promised_date"]) + dt.timedelta(days=1)
            if o["status"] == "cancelled_oos":
                key = "oos"
                refund(o, o["value"], "oos_cancel", "auto")
                if random.random() < .5:
                    ticket(o, "oos", day + dt.timedelta(days=1))
            elif o["status"] == "rto":
                key = "rto"
                refund(o, o["value"], "rto", "auto")
                if random.random() < .7:
                    ticket(o, "wrong_address", ref_day)
            else:
                key = "late" if late else "clean"
                if late:
                    if random.random() < .6:
                        ticket(o, "wismo", ref_day)
                    if random.random() < .18:
                        refund(o, int(o["value"] * random.uniform(.1, .3)), "late_credit",
                               random.choice(["auto", "agent"]))
                if not o["address_ok"] and random.random() < .5:
                    ticket(o, "wrong_address", ref_day)
                p_ret = CATS[o["category"]][1]
                if o["sku"] == "BED-L":
                    p_ret = .30
                if random.random() < p_ret:
                    rtid += 1
                    reason = random.choices(["size", "not_as_described", "damaged", "changed_mind"],
                                            [.7, .15, .05, .1] if o["sku"] == "BED-L" else [.1, .3, .2, .4])[0]
                    returns.append((rtid, o["order_id"], o["category"], o["sku"], reason, o["value"]))
                    ticket(o, "return_req", ref_day + dt.timedelta(days=2))
                if random.random() < .03:
                    ticket(o, "other", ref_day)
            if random.random() > REPEAT_P[key]:
                break
            day += dt.timedelta(days=random.randint(15, 55))

    DB.parent.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE orders(order_id INT, customer_id INT, order_date TEXT, category TEXT, sku TEXT, value INT,
        lane TEXT, courier TEXT, promised_date TEXT, delivered_date TEXT, status TEXT, address_ok INT);
    CREATE TABLE tickets(ticket_id INT, order_id INT, customer_id INT, created_at TEXT, reason TEXT, channel TEXT);
    CREATE TABLE refunds(refund_id INT, order_id INT, amount INT, reason TEXT, approved_by TEXT, created_at TEXT);
    CREATE TABLE returns(return_id INT, order_id INT, category TEXT, sku TEXT, reason TEXT, amount INT);
    """)
    con.executemany("INSERT INTO orders VALUES (:order_id,:customer_id,:order_date,:category,:sku,:value,"
                    ":lane,:courier,:promised_date,:delivered_date,:status,:address_ok)", orders)
    con.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?)", tickets)
    con.executemany("INSERT INTO refunds VALUES (?,?,?,?,?,?)", refunds)
    con.executemany("INSERT INTO returns VALUES (?,?,?,?,?,?)", returns)
    con.commit()
    print(f"orders={len(orders)} tickets={len(tickets)} refunds={len(refunds)} returns={len(returns)} -> {DB}")


if __name__ == "__main__":
    main()
