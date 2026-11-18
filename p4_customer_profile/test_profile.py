"""Checks from spec section 10. Run: py p4_customer_profile/test_profile.py"""
import sqlite3

from build_profile import build, norm_phone
from gen_identity_data import SCHEMA, generate
from refill_outreach import run

DESK = "080-4110 2200"


def db(**tables):
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    for t, rows in tables.items():
        con.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * len(rows[0]))})", rows)
    return con


def cust(con, source, sid):
    row = con.execute("SELECT customer_id FROM customer_xwalk WHERE source = ? AND source_id = ?", (source, sid)).fetchone()
    return row and row[0]


def acct(aid, name, phone, email, address, pet="Bruno", created="2025-01-01"):
    return (aid, name, phone, email, "560001", address, pet, created)


def test_phone_formats():
    assert {norm_phone(p) for p in ["+91 98765 43210", "098765 43210", "98765-43210", "919876543210"]} == {"9876543210"}


def test_shared_desk_phone_flagged_not_merged():
    con = db(accounts=[acct("AC1", "Asha", "9000000001", "a@x.com", "1 MG Rd"),
                       acct("AC2", "Ravi", "9000000002", "r@x.com", "2 CMH Rd", pet="Coco")],
             orders=[("OR1", "AC1", DESK, "", "2026-10-01", "food", 500), ("OR2", "AC2", DESK, "", "2026-10-02", "food", 600)],
             clinic_bookings=[("BK1", DESK, "CL_IND", "Max", "general", "2026-11-20 10:00", "active")])
    build(con)
    assert cust(con, "accounts", "AC1") != cust(con, "accounts", "AC2")
    assert con.execute("SELECT COUNT(*) FROM shared_phones").fetchone()[0] == 1
    assert cust(con, "clinic_bookings", "BK1") is None


def test_oversized_cluster_goes_to_review():
    con = db(accounts=[acct(f"AC{i}", "X", "9111111111", f"{i}@x.com", f"{i} Rd") for i in range(7)])
    build(con)
    assert con.execute("SELECT COUNT(*) FROM customer_xwalk").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM cluster_review").fetchone()[0] == 1


def test_two_numbers_two_accounts_one_customer():
    con = db(accounts=[acct("AC1", "Meera", "+91 98765 43210", "meera@x.com", "1 MG Rd", created="2024-01-01"),
                       acct("AC2", "Meera", "91234-56789", " MEERA@X.COM ", "9 Other Rd")],
             calls=[("CL1", "091234 56789", "2026-11-01 10:00")])
    build(con)
    ids = {cust(con, "accounts", "AC1"), cust(con, "accounts", "AC2"), cust(con, "calls", "CL1")}
    assert len(ids) == 1 and None not in ids


def test_pincode_and_pet_alone_never_merge():
    con = db(accounts=[acct("AC1", "Neha", "9000000001", "n1@x.com", "1 MG Rd"),
                       acct("AC2", "Neha", "9000000002", "n2@x.com", "7 Far Rd")])
    build(con)
    assert cust(con, "accounts", "AC1") != cust(con, "accounts", "AC2")
    assert con.execute("SELECT COUNT(*) FROM match_candidates").fetchone()[0] == 1


def test_generated_end_to_end():
    xwalks = []
    for _ in range(2):  # re-run determinism: same input, same customer_id assignments
        con = sqlite3.connect(":memory:")
        generate(con, n=1500)
        build(con)
        xwalks.append(con.execute("SELECT customer_id, source, source_id, match_tier FROM customer_xwalk ORDER BY 2, 3").fetchall())
    assert xwalks[0] == xwalks[1]

    run(con, purpose="test", run_by="test")  # raises if the read-back does not match
    q = lambda sql: {r[0] for r in con.execute(sql)}
    listed = q("SELECT customer_id FROM outreach_list")
    dnd = q("SELECT customer_id FROM exclusions WHERE reason = 'dnd'")
    assert dnd and not dnd & listed, "DND customers must be excluded and never listed"
    assert not listed & q("SELECT customer_id FROM customer_360 WHERE dnd_flag OR consent_call != 'granted'")
    assert listed == q("SELECT customer_id FROM outreach_assignments WHERE arm = 'contact'")
    hold = q("SELECT customer_id FROM outreach_assignments WHERE arm = 'holdout'")
    assert hold and not hold & listed


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
