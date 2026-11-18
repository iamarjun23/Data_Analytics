"""Synthetic data for Project 1 (refund and claim decisions).

SYNTHETIC: same shape as production, no real customers, thresholds or fraud rules.
Patterns are PLANTED so the engine and the report have something real to find:
  - ~3% of claims come from people running 2-4 accounts (phone typed differently,
    or same address + same pet / same card)
  - late claims are common and genuine on three slow lanes (BLR-DEL, BLR-KOL, BLR-NE)
  - damaged claims with a delivery photo are mostly genuine
  - some households share one address under different surnames (genuine; must not look like abuse)
  - a few heavy, honest customers claim often (where wrong declines come from)
  - a new reason value 'expired' appears in the last week (drift alarm demo)
Run: py p1_refund_decisions/gen_data.py   ->  data/p1.db   (path overridable with P1_DB)
"""
import datetime as dt
import os
import pathlib
import random
import sqlite3

random.seed(11)
DB = pathlib.Path(os.environ.get("P1_DB", pathlib.Path(__file__).resolve().parent.parent / "data" / "p1.db"))
START, END = dt.datetime(2026, 6, 1), dt.datetime(2026, 11, 1, 18, 30)  # UTC; END = 2 Nov 00:00 IST. June-July is history
N_NORMAL, N_FAMILY, N_ABUSERS = 15000, 150, 20
AUDIT_RATE = .30
LANES = {"BLR-BLR": (1, .05, 30), "BLR-HYD": (3, .08, 15), "BLR-MUM": (4, .10, 15),
         "BLR-DEL": (5, .45, 15), "BLR-KOL": (5, .50, 10), "BLR-NE": (7, .55, 5)}  # transit days, P(late), weight
STREETS = ["MG Road", "Indiranagar 2nd Stage", "HSR Layout Sector 1", "Koramangala 5th Block",
           "Whitefield Main Rd", "Jayanagar 4th Block", "Hebbal Ring Rd", "BTM 2nd Stage"]
PINCODES = [f"5600{n:02d}" for n in range(1, 40)]
SURNAMES = ["Rao", "Iyer", "Sharma", "Reddy", "Nair", "Khan", "Das", "Patel", "Gowda", "Menon", "Singh", "Joshi"]
PETS = ["Bruno", "Coco", "Simba", "Luna", "Max", "Milo", "Tiger", "Kiki", "Rocky", "Bella", "Oreo", "Leo"]

accounts, orders, claims = [], [], []


def new_phone():
    return f"{random.choice('6789')}{random.randint(10 ** 8, 10 ** 9 - 1)}"


def new_addr():
    return f"Flat {random.randint(1, 40)}{random.choice('ABCD')}, {random.randint(1, 9999)} {random.choice(STREETS)}"


def account(phone=None, addr=None, pin=None, surname=None, pet=None, pay=None):
    """Raw identity fields, typed the messy way customers type them."""
    aid = len(accounts) + 1
    phone, addr = phone or new_phone(), addr or new_addr()
    accounts.append(dict(
        account_id=aid,
        phone=random.choice([phone, f"+91 {phone[:5]} {phone[5:]}", f"0{phone}", f"91-{phone}"]),
        email=f"u{aid}@example.com",
        address_line=random.choice([addr, addr.upper(), f"  {addr} ", addr.replace(" ", "  ")]),
        pincode=pin or random.choice(PINCODES), surname=surname or random.choice(SURNAMES),
        pet_name=pet or random.choice(PETS), pay_fp=pay or f"fp{random.getrandbits(40):010x}"))
    return accounts[-1]


def order(acc):
    ts = START + dt.timedelta(minutes=random.randint(0, int((END - START).total_seconds() // 60) - 9 * 1440))
    lane = random.choices(list(LANES), [w for *_, w in LANES.values()])[0]
    transit, p_late, _ = LANES[lane]
    promised = (ts + dt.timedelta(days=transit + 1)).date()
    late = random.random() < p_late
    delivered = dt.datetime.combine(promised, dt.time(random.randint(9, 20))) + \
        dt.timedelta(days=random.randint(1, 4) if late else -random.randint(0, 1))
    o = dict(order_id=len(orders) + 1, account_id=acc["account_id"], order_ts_utc=ts, lane=lane,
             value=max(200, int(random.gauss(1400, 700))), promised_date=promised.isoformat(),
             delivered_ts_utc=delivered, status="delivered",
             delivery_proof=random.choices(["otp_ok", "photo_ok", "none"], [.65, .2, .15])[0])
    orders.append(o)
    return o, late


def claim(o, reason, genuine, amount):
    created = o["delivered_ts_utc"] + dt.timedelta(minutes=random.randint(120, 3 * 1440))
    if created < END:
        claims.append(dict(order_id=o["order_id"], account_id=o["account_id"], amount=int(amount),
                           reason=reason, created_at_utc=created, genuine=genuine))


def claims_for(kind, o, late):
    v = o["value"]
    if kind == "abuser":
        if random.random() < .7:
            reason = random.choices(["missing", "damaged", "late"], [.5, .3, .2])[0]
            if reason == "missing" and random.random() < .8:
                o["delivery_proof"] = "otp_ok"
            if reason == "damaged":
                o["delivery_proof"] = "photo_ok" if random.random() < .1 else random.choice(["otp_ok", "none"])
            claim(o, reason, False, v * (random.uniform(.2, .3) if reason == "late" else 1))
        return
    if late and random.random() < .35:
        claim(o, "late", True, v * random.uniform(.1, .3))
    elif random.random() < (.10 if kind == "heavy" else .06):
        reason = random.choices(["damaged", "wrong_item", "missing"], [.45, .3, .25])[0]
        genuine = random.random() < (.9 if v < 1000 else .7)  # opportunists aim at pricier orders
        if reason == "damaged":
            o["delivery_proof"] = "photo_ok" if random.random() < (.75 if genuine else .5) else "none"  # reused photos
        if reason == "missing":
            o["delivery_proof"] = "otp_ok" if random.random() < (.3 if genuine else .7) else "none"
        claim(o, reason, genuine, v * (random.uniform(.3, 1) if reason == "damaged" else 1))
    elif not late and random.random() < .01:
        claim(o, "late", False, v * random.uniform(.1, .2))  # opportunistic: claims late on an on-time order


def main():
    people = []  # (kind, [accounts])
    for _ in range(N_NORMAL):
        people.append(("heavy" if random.random() < .06 else "normal", [account()]))
    for _ in range(N_FAMILY):  # flatmates: same address, different surname, pet, card, phone
        addr, pin, a = new_addr(), random.choice(PINCODES), None
        a = account(addr=addr, pin=pin)
        b = account(addr=addr, pin=pin, surname=random.choice([s for s in SURNAMES if s != a["surname"]]),
                    pet=random.choice([p for p in PETS if p != a["pet_name"]]))
        people += [("normal", [a]), ("normal", [b])]
    for _ in range(N_ABUSERS):  # one person, 2-4 accounts
        phone, addr, pin, pet, pay = new_phone(), new_addr(), random.choice(PINCODES), random.choice(PETS), None
        first = account(phone=phone, addr=addr, pin=pin, pet=pet)
        pay, accs = first["pay_fp"], [first]
        for _ in range(random.randint(1, 3)):
            if random.random() < .4:
                accs.append(account(phone=phone))  # same number, typed differently -> exact link
            elif random.random() < .7:
                accs.append(account(addr=addr, pin=pin, pay=pay))  # same address + same card -> strong
            else:
                accs.append(account(addr=addr, pin=pin, pet=pet))  # same address + same pet -> strong
        people.append(("abuser", accs))

    n_orders = {"normal": (1, 4), "heavy": (10, 16), "abuser": (3, 5)}
    for kind, accs in people:
        for acc in accs:
            for _ in range(random.randint(*n_orders[kind])):
                claims_for(kind, *order(acc))

    claims.sort(key=lambda c: c["created_at_utc"])
    last_week = [c for c in claims if c["created_at_utc"] >= END - dt.timedelta(days=7) and c["reason"] == "damaged"]
    for c in random.sample(last_week, 8):
        c["reason"] = "expired"  # new reason value nobody mapped

    DB.parent.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE accounts(account_id INT PRIMARY KEY, phone TEXT, email TEXT, address_line TEXT, pincode TEXT,
        surname TEXT, pet_name TEXT, pay_fp TEXT);
    CREATE TABLE orders(order_id INT PRIMARY KEY, account_id INT, order_ts_utc TEXT, lane TEXT, value INT,
        promised_date TEXT, delivered_ts_utc TEXT, status TEXT, delivery_proof TEXT);
    CREATE TABLE claims(claim_id INT PRIMARY KEY, order_id INT, account_id INT, amount INT, reason TEXT,
        created_at_utc TEXT);
    CREATE TABLE claim_audit(claim_id INT PRIMARY KEY, is_genuine INT);          -- ground truth, sample only
    CREATE TABLE agent_decisions(claim_id INT PRIMARY KEY, decision TEXT, handle_min INT);  -- today's manual process
    """)
    con.executemany("INSERT INTO accounts VALUES (:account_id,:phone,:email,:address_line,:pincode,:surname,"
                    ":pet_name,:pay_fp)", accounts)
    con.executemany("INSERT INTO orders VALUES (:order_id,:account_id,:order_ts_utc,:lane,:value,:promised_date,"
                    ":delivered_ts_utc,:status,:delivery_proof)",
                    [{**o, "order_ts_utc": f"{o['order_ts_utc']:%Y-%m-%d %H:%M:%S}",
                      "delivered_ts_utc": f"{o['delivered_ts_utc']:%Y-%m-%d %H:%M:%S}"} for o in orders])
    for cid, c in enumerate(claims, 1):
        con.execute("INSERT INTO claims VALUES (?,?,?,?,?,?)", (cid, c["order_id"], c["account_id"], c["amount"],
                    c["reason"], f"{c['created_at_utc']:%Y-%m-%d %H:%M:%S}"))
        if random.random() < AUDIT_RATE:
            con.execute("INSERT INTO claim_audit VALUES (?,?)", (cid, int(c["genuine"])))
        # agents see one account at a time, so they approve most claims, abuse included
        approve = random.random() < (.95 if c["genuine"] else .55)
        con.execute("INSERT INTO agent_decisions VALUES (?,?,?)",
                    (cid, "approve" if approve else "decline", random.randint(8, 16)))
    con.commit()
    abuse = {a["account_id"] for kind, accs in people if kind == "abuser" for a in accs}
    print(f"accounts={len(accounts)} orders={len(orders)} claims={len(claims)} "
          f"multi-account claims={sum(c['account_id'] in abuse for c in claims) / len(claims):.1%} -> {DB}")


if __name__ == "__main__":
    main()
