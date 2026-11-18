"""Runnable checks: each signal on fixture orders, actions, compliance filter, holdout, templates, and a
read-back of stored rows from one run on a small test world.
Run: py p2_order_delay_early_warning/test_job.py
"""
import datetime as dt
import pathlib
import tempfile

import gen
import job

NOW = dt.datetime(2026, 10, 10, 2, 30)  # 08:00 IST
MORNING, EVENING = NOW + dt.timedelta(hours=1), dt.datetime(2026, 10, 10, 15, 0)  # 09:00 / 20:30 IST
MOVING = {"PLACED", "PACKED", "SHIPPED", "IN_TRANSIT"}


def h(x):
    return dt.timedelta(hours=x)


def order(**kw):
    o = dict(lane_rate=.10, codes=MOVING, last_scan=NOW - h(10), order_ts=NOW - h(48), category="Dog food",
             address_ok=1, promised=dt.date(2026, 10, 15))
    return {**o, **kw}


# signals
assert job.signals(order(), NOW) == []
assert job.signals(order(lane_rate=.30), NOW) == ["lane_prior"]
assert job.signals(order(last_scan=NOW - h(37)), NOW) == ["scan_gap"]
assert job.signals(order(last_scan=NOW - h(35)), NOW) == []
assert job.signals(order(codes={"PLACED"}, order_ts=NOW - h(13), category="Pharmacy"), NOW) == ["packing_age"]
assert job.signals(order(codes={"PLACED"}, order_ts=NOW - h(13)), NOW) == []  # Dog food SLA is 24h
assert "stock" in job.signals(order(codes={"PLACED", "OOS"}), NOW)
assert job.signals(order(address_ok=0), NOW) == ["address"]
assert job.signals(order(promised=dt.date(2026, 10, 12)), NOW) == ["promise"]
assert job.signals(order(promised=dt.date(2026, 10, 12), codes=MOVING | {"OUT_FOR_DELIVERY"}), NOW) == []

# score, flag, action
assert job.score(["lane_prior"]) < job.FLAG_AT <= job.score(["lane_prior", "promise"])
assert job.score(list(job.W)) == 1.0
assert job.choose_action(["stock", "address"], 1, 100) == "oos_offer"
assert job.choose_action(["address"], .5, 100) == "address_confirm"
assert job.choose_action(["scan_gap", "lane_prior", "promise"], .75, 3000) == "callback"
assert job.choose_action(["scan_gap", "lane_prior", "promise"], .75, 900) == "delay_notice"

# compliance filter
assert job.downgrade("callback", {"call", "sms"}, 1) == ("delay_notice", [("callback", "dnd")])
assert job.downgrade("callback", {"sms"}, 0) == ("delay_notice", [("callback", "no_consent")])
assert job.downgrade("delay_notice", set(), 1) == ("delay_notice", [])  # DND does not block order updates
assert job.route("delay_notice", "whatsapp", {"sms"}, MORNING, False) == ("sms", None)
assert job.route("delay_notice", "whatsapp", set(), MORNING, False) == (None, "no_consent")
assert job.route("delay_notice", "sms", {"sms"}, NOW, False) == (None, "quiet_hours")       # 08:00 IST
assert job.route("delay_notice", "sms", {"sms"}, EVENING, False) == ("sms", None)          # done by 21:00
assert job.route("callback", "sms", {"call"}, EVENING, False) == (None, "quiet_hours")     # call would run past 21:00
assert job.route("callback", "sms", {"call"}, MORNING, False) == ("call", None)
assert job.route("delay_notice", "sms", {"sms"}, MORNING, True) == (None, "cap")

# holdout: about 10%, stable
share = sum(map(job.is_holdout, range(1, 20001))) / 20000
assert .09 < share < .11, share
assert [job.is_holdout(i) for i in range(50)] == [job.is_holdout(i) for i in range(50)]

# templates carry no medicine / pet-health / product detail
for tid, text in job.TEMPLATES.items():
    assert not any(w in text.lower() for w in ("medicine", "pharmacy", "diagnos", "{category", "{sku")), tid

# test world: one run, then read back what was stored
with tempfile.TemporaryDirectory() as d:
    con = gen.world(pathlib.Path(d) / "t.db", dt.date(2026, 9, 1), dt.date(2026, 10, 5), per_day=40)
    con.executescript(job.SCHEMA)
    rec = job.run(con, dt.datetime(2026, 10, 5, 2, 30))
    assert rec["flagged"] > 0 and rec["listed"] > 0, rec
    cols = {c[1] for c in con.execute("PRAGMA table_info(outreach_list)")}
    assert not cols & {"phone", "email", "address", "account_id", "category", "value"}, cols
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    assert q("SELECT COUNT(*) FROM risk_flags f WHERE NOT EXISTS (SELECT 1 FROM outreach_list l WHERE "
             "l.order_id = f.order_id) AND NOT EXISTS (SELECT 1 FROM exclusions e WHERE e.order_id = f.order_id)") == 0
    assert q("SELECT COUNT(*) FROM outreach_list WHERE is_holdout = 1 AND channel IS NOT NULL") == 0
    assert q("SELECT COUNT(*) FROM outreach_list l JOIN orders o USING (order_id) JOIN consent c ON "
             "c.customer_id = o.account_id AND c.channel = l.channel WHERE c.consent_status <> 'granted'") == 0
    again = job.run(con, dt.datetime(2026, 10, 5, 3, 0))  # same morning rerun: nothing new to list
    assert again["listed"] == 0, again
    con.close()

print("all checks passed")
