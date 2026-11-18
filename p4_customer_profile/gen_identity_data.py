"""Synthetic multi-source identity data for Project 4 (one customer profile).
SYNTHETIC: no real customer, prescription or pet-health data. Fixed seed, so every run is identical.
Run: py p4_customer_profile/gen_identity_data.py  ->  data/p4.db

Planted patterns: people with two numbers and two accounts, messy phone formats, a clinic desk
phone that ties strangers together, the same pet booked at two clinics under two numbers,
call and consult records with no account, tickets with no order id, a pharmacy feed that lags.
"""
import collections
import datetime as dt
import pathlib
import random
import sqlite3

DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "p4.db"
AS_OF = dt.date(2026, 11, 15)   # snapshot date: every "today" in P4 is this day (IST)
DESK_PHONE = "080-4110 2200"    # a clinic front desk; some customers ship orders to it

SCHEMA = """
CREATE TABLE accounts(account_id TEXT, first_name TEXT, phone TEXT, email TEXT, pincode TEXT, address TEXT, pet_name TEXT, created_at TEXT);
CREATE TABLE orders(order_id TEXT, account_id TEXT, ship_phone TEXT, address TEXT, order_date TEXT, category TEXT, value INT);
CREATE TABLE tickets(ticket_id TEXT, order_id TEXT, phone TEXT, created_at TEXT, csat INT);
CREATE TABLE calls(call_id TEXT, phone TEXT, start_ts TEXT);
CREATE TABLE pharmacy_rx(rx_id TEXT, account_id TEXT, pet_id TEXT, medicine_code TEXT, refill_days INT, last_fill_date TEXT);
CREATE TABLE vet_consults(consult_id TEXT, phone TEXT, pet_id TEXT, consult_date TEXT, specialty TEXT);
CREATE TABLE clinic_bookings(booking_id TEXT, phone TEXT, clinic_id TEXT, pet_name TEXT, specialty TEXT, slot_ts TEXT, status TEXT);
CREATE TABLE consent(phone TEXT, channel TEXT, consent_status TEXT, dnd_flag INT, updated_at TEXT);
CREATE TABLE contact_log(phone TEXT, channel TEXT, contacted_at TEXT, script_id TEXT);
-- answer key, used only to score the matching; the build never reads it
CREATE TABLE truth_person(source TEXT, source_id TEXT, person_id INT);
"""

FIRST = ("Aarav Aditi Akash Ananya Arjun Bhavna Chetan Deepa Divya Gaurav Isha Karan Kavya Lakshmi Manoj "
         "Meera Naveen Neha Nikhil Pooja Priya Rahul Ravi Riya Rohan Sanjay Shreya Sneha Suresh Tanvi "
         "Varun Vidya Vikram Yash Zoya Farhan Imran Joseph Mary Anil").split()
PETS = ("Bruno Coco Max Simba Leo Bella Milo Luna Rocky Tiger Oreo Sheru Snowy Buddy Lucy Charlie Ginger "
        "Pepper Mango Kiwi Toffee Cookie Jimmy Tommy Daisy Lily Rani Raja Chikoo Laddoo Biscuit Ziggy "
        "Bolt Cleo Momo Nala Rio Sasha Teddy Whiskey").split()
STREETS = ["100 Feet Rd Indiranagar", "27th Main HSR Layout", "ITPL Rd Whitefield", "15th Cross JP Nagar",
           "8th Cross Malleshwaram", "80 Feet Rd Koramangala", "CMH Rd", "Sarjapur Rd", "Bannerghatta Rd",
           "Hennur Main Rd", "Old Airport Rd", "Kanakapura Rd"]
PINS = [f"5600{n:02d}" for n in range(1, 80, 2)]
CLINICS = ["CL_IND", "CL_HSR", "CL_WHF", "CL_JPN", "CL_MLM"]
SPECS = ["general", "derma", "ortho", "dental"]
SLOTS = ["09:30", "11:00", "12:30", "15:00", "16:30", "18:00"]
CHANNELS = ["call", "whatsapp", "sms"]


def generate(con, n=5000, seed=7):
    r = random.Random(seed)
    T, truth, seq, used, people = collections.defaultdict(list), [], collections.Counter(), set(), []

    def nid(prefix):
        seq[prefix] += 1
        return f"{prefix}{seq[prefix]:06d}"

    def put(table, pid, *row):
        T[table].append(row)
        truth.append((table, row[0], pid))

    def new_phone():
        while True:
            p = r.choice("6789") + "".join(r.choices("0123456789", k=9))
            if p not in used:
                used.add(p)
                return p

    def messy(p):  # one number, written the ways different systems write it
        a, b = p[:5], p[5:]
        return r.choice([f"+91 {a} {b}", f"0{a} {b}", f"{a}-{b}", f"91{p}", p, f"+91-{p}"])

    def ago(lo, hi):
        return AS_OF - dt.timedelta(days=r.randint(lo, hi))

    def address(pin):
        return f"{r.randint(1, 999)}, {r.choice(STREETS)}, Bangalore {pin}"

    for pid in range(1, n + 1):
        name, pet, pin = r.choice(FIRST), r.choice(PETS), r.choice(PINS)
        addr = address(pin)
        phones = [new_phone()] + ([new_phone()] if r.random() < 0.15 else [])
        email = f"{name.lower()}{pid}@example.com"
        accts = [nid("AC")]
        put("accounts", pid, accts[0], name, messy(phones[0]), email, pin, addr, pet, str(ago(400, 900)))

        if r.random() < 0.10:  # second account; `kind` is the only thing tying it to the first
            kind = r.choice("ABCD")  # A same phone, B same email, C same address+pet, D nothing but a shipped order
            if kind != "A" and len(phones) == 1:
                phones.append(new_phone())
            p = phones[0] if kind == "A" else phones[1]
            em = f" {email.upper()} " if kind == "B" else f"{name.lower()}.{pet.lower()}{pid}@example.com"
            ad = addr.upper().replace(",", " ") if kind == "C" else address(pin)
            accts.append(nid("AC"))
            put("accounts", pid, accts[1], name, messy(p), em, pin, ad, pet, str(ago(30, 400)))

        gap = r.randint(15, 75)
        stop = ago(0, 250) if r.random() < 0.35 else AS_OF  # lapsed customers stop ordering
        d = ago(330, 365)
        while d <= stop:
            ship = phones[-1] if len(phones) > 1 and r.random() < 0.3 else phones[0]
            oid = nid("OR")
            put("orders", pid, oid, r.choice(accts), messy(ship), addr, str(d),
                r.choice(["food", "food", "supplies", "treats"]), r.randint(300, 3500))
            if r.random() < 0.08:
                when = min(d + dt.timedelta(days=r.randint(1, 10)), AS_OF)
                put("tickets", pid, nid("TK"), None if r.random() < 0.04 else oid, messy(ship), str(when),
                    r.choice([None, 1, 2, 3, 4, 4, 5, 5]))
            d += dt.timedelta(days=max(3, round(gap * r.uniform(0.7, 1.3))))

        if r.random() < 0.12:  # chronic medication: one pharmacy order per refill cycle
            refill = r.choice([30, 30, 30, 60, 90])
            d, stop_rx = ago(200, 365), (ago(0, 200) if r.random() < 0.4 else AS_OF)
            fills = []
            while d <= stop_rx:
                fills.append(d)
                put("orders", pid, nid("OR"), accts[0], messy(phones[0]), addr, str(d), "pharmacy", r.randint(400, 2500))
                d += dt.timedelta(days=refill + r.randint(-3, 5))
            last = fills[-2] if len(fills) > 1 and r.random() < 0.1 else fills[-1]  # feed lags one fill behind
            put("pharmacy_rx", pid, nid("RX"), accts[0], f"PT{pid:05d}", f"M{r.randint(1, 15):02d}", refill, str(last))

        for _ in range(r.choice([0, 0, 1, 1, 2, 3])):
            put("calls", pid, nid("CL"), messy(r.choice(phones)), f"{ago(0, 180)} {r.randint(9, 20):02d}:{r.randint(0, 59):02d}")

        if r.random() < 0.10:
            for _ in range(r.randint(1, 2)):
                put("vet_consults", pid, nid("VC"), messy(r.choice(phones)), f"VP{pid:05d}", str(ago(0, 400)), r.choice(SPECS))

        if r.random() < 0.10:
            for _ in range(r.randint(1, 2)):
                s = AS_OF + dt.timedelta(days=r.randint(-300, 30))
                status = "active" if s >= AS_OF else r.choice(["completed", "completed", "cancelled"])
                put("clinic_bookings", pid, nid("BK"), messy(r.choice(phones)), r.choice(CLINICS), pet,
                    r.choice(SPECS), f"{s} {r.choice(SLOTS)}", status)
        people.append((pid, accts, phones, pet))

    # Same pet, same specialty, same week, two clinics, two numbers.
    monday = AS_OF + dt.timedelta(days=7 - AS_OF.weekday())
    for pid, accts, phones, pet in r.sample([p for p in people if len(p[2]) > 1], 25):
        week, spec = monday + dt.timedelta(weeks=r.randint(0, 2)), r.choice(SPECS)
        for ph, clinic in zip(phones, r.sample(CLINICS, 2)):
            slot = week + dt.timedelta(days=r.randint(0, 4))
            put("clinic_bookings", pid, nid("BK"), messy(ph), clinic, pet, spec, f"{slot} {r.choice(SLOTS)}", "active")

    # Clinic desk phone: six customers ship an order there, a dozen walk-ins book from it.
    for pid, accts, phones, pet in r.sample(people, 6):
        put("orders", pid, nid("OR"), accts[0], DESK_PHONE, "Clinic, 100 Feet Rd Indiranagar", str(ago(5, 90)), "food", r.randint(300, 2000))
    for pid, accts, phones, pet in r.sample(people, 12):
        s = AS_OF + dt.timedelta(days=r.randint(1, 20))
        put("clinic_bookings", pid, nid("BK"), DESK_PHONE, "CL_IND", pet, r.choice(SPECS), f"{s} {r.choice(SLOTS)}", "active")

    # People who never made an account.
    for k in range(1, 401):  # enquiries and wrong numbers
        put("calls", -k, nid("CL"), messy(new_phone()), f"{ago(0, 180)} {r.randint(9, 20):02d}:00")
    for k in range(401, 501):  # consult-only pet parents
        p = new_phone()
        for _ in range(r.randint(1, 2)):
            put("vet_consults", -k, nid("VC"), messy(p), f"VX{k:05d}", str(ago(0, 400)), r.choice(SPECS))

    for pid, accts, phones, pet in people:
        for p in phones:
            if r.random() < 0.05:
                continue  # consent never captured for this number
            dnd = int(r.random() < 0.08)
            for ch in CHANNELS:
                T["consent"].append((messy(p), ch, "granted" if r.random() < 0.85 else "revoked", dnd, str(ago(0, 500))))
        if r.random() < 0.03:
            T["contact_log"].append((messy(phones[0]), "call", f"{ago(1, 5)} 11:00", "SCR_OLD"))

    T["truth_person"] = truth
    con.executescript(SCHEMA)
    for table, rows in T.items():
        con.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
    con.commit()
    return {t: len(rows) for t, rows in T.items()}


if __name__ == "__main__":
    DB.parent.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    with sqlite3.connect(DB) as con:
        for t, c in generate(con).items():
            print(f"{t:16} {c:7}")
    print(f"-> {DB}")
