"""Tests for the CX pipeline. Run (from p3_cx_pipeline/): py test_pipeline.py   (or: py -m pytest test_pipeline.py)
Each test builds its own throwaway SQLite file and its own mock API.
"""
import datetime as dt
import json
import pathlib
import tempfile

from mock_api import IST, UTC, MockAPI, iso
from pipeline import checks, disagree, metrics, reconcile, run

run.BACKOFF_S = 0  # no waiting on the mock rate limit
NOW = dt.datetime(2026, 10, 20, 0, 30, tzinfo=UTC)  # 06:00 IST
DAY = dt.timedelta(days=1)


def fresh():
    return run.connect(pathlib.Path(tempfile.mkdtemp()) / "t.db")


def count(con, table):
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def snapshot(con, table):
    return sorted(tuple(r) for r in con.execute(f"SELECT * FROM {table}"))


def test_units():
    assert run.parse_dt("2026-11-01T18:00:00Z", "iso") == "2026-11-01 18:00:00"
    assert run.parse_dt("01/11/2026 23:30", "dmy_ist") == "2026-11-01 18:00:00"
    assert run.parse_dt(1761955200000, "epoch_ms") == "2025-11-01 00:00:00"
    # 23:30 IST on 1 Nov is 18:00 UTC: the IST anchor keeps it on 1 Nov, and 00:30 IST on 2 Nov is still 1 Nov in UTC
    assert metrics.day("2026-11-01 18:00:00") == "2026-11-01"
    assert metrics.day("2026-11-01 19:00:00") == "2026-11-02"
    assert metrics.day("2026-11-01 19:00:00", "UTC") == "2026-11-01"
    con = fresh()
    maps = run.load_maps(con)
    rec = dict(ticket_id="T1", created_at="2026-11-01T18:00:00Z", updated_at="2026-11-01T18:00:00Z",
               status="pending_customer", channel="wa_inbound", queue="q_refunds", order_ref=None,
               resolved_at=None, requester_email="a@example.com")
    row = run.transform("ticketing", rec, maps)
    assert (row["status_group"], row["channel_group"], row["team"]) == ("waiting", "whatsapp", "refunds")
    assert row["requester_email_hash"] != "a@example.com" and "requester_email" not in row
    assert run.transform("ticketing", rec | {"status": "brand_new"}, maps)["status_group"] == "UNMAPPED"
    assert run.transform("ticketing", rec | {"channel": None}, maps) is None  # required blank -> rejected
    assert run.check_contract("ticketing", [rec]) == ([], [])


def test_renamed_field_fails_before_load():
    con = fresh()
    api = MockAPI(NOW, drift={"rename": ("ticketing", "channel", "channel_name")})
    assert run.run(con, api, NOW) == 1
    log = {r["table_name"]: r for r in con.execute("SELECT * FROM run_log")}
    assert log["ticketing"]["status"] == "failed" and "channel" in log["ticketing"]["error"]
    assert count(con, "raw_ticketing") == 0 and count(con, "stg_ticketing") == 0  # nothing loaded
    assert count(con, "stg_orders") > 0  # other sources still load
    assert con.execute("SELECT COUNT(*) FROM alerts WHERE level='fail' AND table_name='ticketing'").fetchone()[0]


def test_type_change_fails_run():
    con = fresh()
    assert run.run(con, MockAPI(NOW, drift={"type": ("telephony", "duration_s", "")}), NOW) == 1
    assert count(con, "stg_telephony") == 0


def test_new_status_goes_to_unmapped_and_alerts():
    con = fresh()
    assert run.run(con, MockAPI(NOW, drift={"new_value": ("ticketing", "status", "escalated_l2")}), NOW) == 0
    n = con.execute("SELECT COUNT(*) FROM stg_ticketing WHERE status='escalated_l2' AND status_group='UNMAPPED'").fetchone()[0]
    assert n > 0
    assert con.execute("SELECT COUNT(*) FROM alerts WHERE message LIKE 'UNMAPPED status%escalated_l2%'").fetchone()[0]
    res = {(r[2], r[3]): r[6] for r in checks.run_checks(con, NOW)}
    assert res[("unmapped_share:status", "ticketing")] == "warn"


def test_late_edit_caught_by_lookback_not_duplicated():
    """An order edited with updated_at two days back, visible only today: look-back catches it, 0-day misses it."""
    results = {}
    for lookback in (0, 3):
        con, api = fresh(), MockAPI(NOW)
        run.run(con, api, NOW, lookback_days=lookback)
        before = count(con, "stg_orders")
        oid = con.execute("SELECT order_id FROM stg_orders WHERE status='delivered' AND updated_at < ? ORDER BY order_id",
                          ((NOW - 3 * DAY).strftime(run.FMT),)).fetchone()[0]
        payload = json.loads(con.execute("SELECT payload FROM raw_orders WHERE record_key=?", (oid,)).fetchone()[0])
        later = NOW + DAY
        edited = payload | {"delivered_at": "01/10/2026 09:00", "updated_at": iso(NOW - 2 * DAY)}
        api.add_version("orders", edited, visible_at=later)
        api.now = later
        run.run(con, api, later, lookback_days=lookback)
        got = con.execute("SELECT delivered_at FROM stg_orders WHERE order_id=?", (oid,)).fetchone()[0]
        results[lookback] = got == "2026-10-01 03:30:00"
        assert count(con, "stg_orders") >= before
        assert con.execute("SELECT COUNT(*) FROM stg_orders WHERE order_id=?", (oid,)).fetchone()[0] == 1
    assert results == {0: False, 3: True}


def test_edits_update_rows_and_match_source():
    """Two days of edits (reopens, deliveries, bot outcomes): staging equals the vendor's current state, one row per key."""
    con, api = fresh(), MockAPI(NOW)  # generate up to NOW, then step the clock
    for d in (2, 1, 0):
        api.now = NOW - d * DAY
        assert run.run(con, api, api.now) == 0
    for s, c in run.CONTRACTS.items():
        src = {r[c["key"]]: r["updated_at"] for _, _, r in api._current(s)}
        stg = dict(con.execute(f"SELECT {c['key']}, updated_at FROM stg_{s}").fetchall())
        assert len(stg) == len(src)
        assert all(run.parse_dt(src[k], "iso") == v for k, v in stg.items()), s
    assert reconcile.reconcile_counts(con, api, NOW) == []


def test_idempotent():
    con, api = fresh(), MockAPI(NOW)
    run.run(con, api, NOW)
    first = {s: snapshot(con, f"stg_{s}") for s in run.CONTRACTS}
    marts = snapshot(con, "mart_daily")
    run.run(con, api, NOW)
    assert first == {s: snapshot(con, f"stg_{s}") for s in run.CONTRACTS}
    assert marts == snapshot(con, "mart_daily")
    last = con.execute("SELECT SUM(rows_new), SUM(rows_changed) FROM run_log WHERE run_id = "
                       "(SELECT run_id FROM run_log ORDER BY rowid DESC LIMIT 1)").fetchone()
    assert tuple(last) == (0, 0)


def test_backfill_snapshots_then_reloads():
    con, api = fresh(), MockAPI(NOW)
    run.run(con, api, NOW)
    con.execute("DELETE FROM stg_bot WHERE started_at >= '2026-10-15'")  # simulate lost rows
    con.commit()
    run.run(con, api, NOW, backfill=(dt.date(2026, 10, 14), dt.date(2026, 10, 20)), sources=["bot"])
    assert count(con, "bak_stg_bot") > 0
    assert reconcile.reconcile_counts(con, api, NOW) == []


def test_dashboard_numbers_equal_definitions_and_tool_finds_timezone():
    con, api = fresh(), MockAPI(NOW)
    run.run(con, api, NOW)
    a, rows, chosen, together = disagree.explain(con, "tickets_created", "2026-10-10", "2026-10-16", 0, NOW)
    mart = con.execute("SELECT SUM(value) FROM mart_daily WHERE metric='tickets_created' "
                       "AND day BETWEEN '2026-10-10' AND '2026-10-16'").fetchone()[0]
    assert a == mart  # dashboard and tool share one definition
    utc = next(r["value"] for r in rows if r["cause"] == "timezone")
    a, rows, chosen, together = disagree.explain(con, "tickets_created", "2026-10-10", "2026-10-16", utc, NOW)
    assert chosen == ["timezone"] and together == utc  # B built on UTC dates: 100% explained by timezone


def test_ist_dates_match_vendor_report():
    con, api = fresh(), MockAPI(NOW)
    run.run(con, api, NOW)
    src = api.daily_counts("orders")
    day = (NOW.astimezone(IST).date() - 3 * DAY).isoformat()
    assert sum(n for (d, _), n in src.items() if d == day) == con.execute(
        "SELECT COUNT(*) FROM mart_orders WHERE created_date_ist = ?", (day,)).fetchone()[0]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in tests:
        f()
        print("ok  ", n)
    print(f"{len(tests)} tests passed")
