"""One runnable check for project 5. Run: py -m unittest p5_conversation_qa/test_p5.py  (from the repo root)
No database needed: every test builds its own fixtures."""
import datetime as dt
import sqlite3
import sys
import pathlib
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import auditor  # noqa: E402
import breaker  # noqa: E402
import report  # noqa: E402
import webhook  # noqa: E402

V1, V2 = auditor.load_rubric(1), auditor.load_rubric(2)
T0 = dt.datetime(2026, 11, 5, 6, 0)


def conv(*msgs, handler="bot", outcome="resolved"):
    return {"handler": handler, "outcome": outcome, "messages": [{"role": r, "text": t} for r, t in msgs]}


def tk(bot_outcome="resolved", summary=None, status="solved"):
    return {"bot_outcome": bot_outcome, "handover_summary": summary, "status": status}


class Redaction(unittest.TestCase):
    def test_pii_removed_order_id_kept(self):
        r = auditor.redact("Hi, I'm Ananya Rao. My number is 9876543210. Email ananya.rao@example.com. "
                           "Change ORD123456 to 12 MG Road, Indiranagar 560038.")
        for pii in ("Ananya", "9876543210", "@example.com", "MG Road", "560038"):
            self.assertNotIn(pii, r)
        self.assertIn("ORD123456", r)
        self.assertIn("[NAME]", r)


class Rules(unittest.TestCase):
    def s(self, c, t=None, rb=V2):
        return auditor.score_rules(c, t or tk(), rb)[0]

    def test_outcome_recorded(self):
        self.assertEqual(self.s(conv(("customer", "hi")))["outcome_recorded"], "yes")
        self.assertEqual(self.s(conv(("customer", "hi")), tk(bot_outcome=None))["outcome_recorded"], "no")

    def test_handover_context(self):
        c = conv(("customer", "late"), ("bot", "Let me connect you to an agent."), ("agent", "Hi"), outcome="handed_over")
        good, vague, empty = tk(summary="intent=delivery_delay; order=ORD123456"), tk(summary="intent=unknown; tried=none"), tk(summary="")
        self.assertEqual(self.s(c, good)["handover_with_context"], "yes")
        self.assertEqual(self.s(c, empty)["handover_with_context"], "no")
        self.assertEqual(self.s(c, vague, V1)["handover_with_context"], "yes")  # v1 gap
        self.assertEqual(self.s(c, vague, V2)["handover_with_context"], "no")   # v2 fix
        self.assertEqual(self.s(conv(("customer", "hi")))["handover_with_context"], "not_applicable")

    def test_medicine(self):
        ask = ("customer", "I need a refill of NexGard for my dog.")
        ok = conv(ask, ("bot", "Your NexGard refill is booked. Please consult your vet before starting or changing any medicine."))
        miss = conv(ask, ("bot", "Your NexGard refill is booked."))
        unsafe = conv(ask, ("bot", "You can give 1 tablet twice daily. Please consult your vet before starting or changing any medicine."))
        alt = conv(ask, ("bot", "Booked. Please check with your veterinarian before changing any medicine."))
        count_only = conv(ask, ("bot", "Your refill of 3 tablets is booked. Please consult your vet first."))
        self.assertEqual(self.s(ok)["medicine_safety"], "ok")
        self.assertEqual(self.s(miss)["medicine_safety"], "missing_disclaimer")
        self.assertEqual(self.s(unsafe)["medicine_safety"], "unsafe")
        self.assertEqual(self.s(alt, rb=V1)["medicine_safety"], "missing_disclaimer")
        self.assertEqual(self.s(alt, rb=V2)["medicine_safety"], "ok")
        self.assertEqual(self.s(count_only)["medicine_safety"], "ok")  # a quantity is not dosing advice
        self.assertEqual(self.s(conv(("customer", "Where is my order?")))["medicine_safety"], "not_applicable")

    def test_policy_bot_vs_agent(self):
        self.assertEqual(self.s(conv(("bot", "I have approved your refund for ORD123456.")))["policy_followed"], "no")
        self.assertEqual(self.s(conv(("agent", "I have approved your refund for ORD123456."), handler="agent"))["policy_followed"], "yes")
        subtle = conv(("bot", "Don't worry, you'll get your money back."))
        self.assertEqual(self.s(subtle, rb=V1)["policy_followed"], "yes")
        self.assertEqual(self.s(subtle, rb=V2)["policy_followed"], "no")

    def test_sentiment(self):
        sarcasm = conv(("customer", "Great, thanks for nothing."))
        self.assertEqual(self.s(sarcasm, rb=V1)["sentiment"], "positive")
        self.assertEqual(self.s(sarcasm, rb=V2)["sentiment"], "negative")
        self.assertEqual(self.s(conv(("customer", "ok")))["sentiment"], "neutral")


class ModelSchema(unittest.TestCase):
    good = {"resolved": {"value": "yes", "evidence": "Sorted."}, "policy_followed": {"value": "yes", "evidence": ""},
            "sentiment": {"value": "negative", "evidence": "useless"}}

    def test_good_passes(self):
        self.assertTrue(auditor.validate_model_output(self.good))

    def test_malformed_rejected(self):
        bad = [dict(self.good, resolved={"value": "maybe", "evidence": ""}),
               {k: v for k, v in self.good.items() if k != "sentiment"},
               dict(self.good, extra={"value": "x", "evidence": ""}),
               dict(self.good, sentiment={"value": "negative"}),
               dict(self.good, sentiment={"value": "negative", "evidence": 3}),
               "not json"]
        for b in bad:
            self.assertFalse(auditor.validate_model_output(b), b)

    def test_schema_fields_match_rubric(self):
        sch = auditor.model_schema()["properties"]
        for f in auditor.MODEL_FIELDS:
            self.assertEqual(sch[f]["properties"]["value"]["enum"], V2["fields"][f]["values"])


class Webhook(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("CREATE TABLE tickets(ticket_id TEXT PRIMARY KEY, conversation_id TEXT, status TEXT, bot_outcome TEXT,"
                         " handover_flag INT, handover_summary TEXT, updated_at_utc TEXT)")
        self.con.execute("INSERT INTO tickets VALUES ('T1', 'C1', 'open', NULL, 0, NULL, NULL)")
        webhook.ensure_tables(self.con)
        self.api = webhook.FakeTicketAPI(self.con)

    def send(self, p, at=T0, api=None):
        return webhook.send_with_readback(self.con, api or self.api, p, at)

    def test_happy_path_reads_back(self):
        p = webhook.build_payload("C1", "T1", "handed_over", "intent=refund; order=ORD123456; tried=policy answer")
        self.assertEqual(self.send(p), "ok")
        self.assertEqual(self.api.get("T1")["handover_summary"], p["handover_summary"])

    def test_edge_cases(self):
        self.assertEqual(self.send(webhook.build_payload("C1", "T1", "handed_over", "")), "ok")  # empty summary still stores
        self.con.execute("DELETE FROM processed_events")
        self.assertEqual(self.send(webhook.build_payload("C1", "T1", "handed_over", "émoji 🐶 \"quotes\" <b>&amp;</b> ✓")), "ok")
        self.con.execute("DELETE FROM processed_events")
        self.assertEqual(self.send(webhook.build_payload("C1", "T1", "handed_over", "x" * 5000)), "ok")  # cut to limit before send

    def test_readback_catches_silent_truncation(self):
        p = webhook.build_payload("C1", "T1", "handed_over", "")
        p["handover_summary"] = "x" * 5000  # bypass our cut: vendor truncates silently
        self.assertEqual(self.send(p), "mismatch")

    def test_duplicate_event_no_second_update(self):
        p = webhook.build_payload("C1", "T1", "resolved", None)
        self.assertEqual(self.send(p), "ok")
        dup = dict(p, bot_outcome="abandoned")  # same event_id, different body
        self.send(dup)
        self.assertEqual(self.api.get("T1")["bot_outcome"], "resolved")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0], 1)

    def test_wrong_ticket_parks_without_retry(self):
        self.assertEqual(self.send(webhook.build_payload("C9", "T999", "resolved", None)), "parked")
        self.assertEqual(self.con.execute("SELECT MAX(attempt), MAX(http_status) FROM webhook_log").fetchone(), (1, 404))

    def test_invalid_payload_parked_not_sent(self):
        self.assertEqual(self.send(webhook.build_payload("C1", "T1", None, None)), "parked")
        self.assertIsNone(self.api.get("T1")["bot_outcome"])

    def test_outage_retries_then_parks_or_recovers(self):
        down = webhook.FakeTicketAPI(self.con, down=(T0, T0 + dt.timedelta(minutes=30)))
        self.assertEqual(self.send(webhook.build_payload("C1", "T1", "resolved", None), api=down), "parked")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM webhook_log WHERE http_status = 503").fetchone()[0], 4)
        short = webhook.FakeTicketAPI(self.con, down=(T0, T0 + dt.timedelta(minutes=1, seconds=30)))  # 3rd try lands
        self.assertEqual(self.send(webhook.build_payload("C1", "T1", "resolved", None), api=short), "ok")

    def test_no_text_in_log(self):
        self.send(webhook.build_payload("C1", "T1", "handed_over", "SECRET-ish customer words"))
        rows = self.con.execute("SELECT * FROM webhook_log").fetchall()
        self.assertFalse(any("customer words" in str(r) for r in rows))


class Breaker(unittest.TestCase):
    def week(self, bad_from, bad_to, rate=.3):
        """60 bot sessions/hour for 6 hours; no-outcome share `rate` inside the bad window, 0 outside."""
        rows, t = [], T0
        while t < T0 + dt.timedelta(hours=6):
            bad = bad_from <= t < bad_to
            rows.append((t, int(bad and (t.minute % 10) < 10 * rate), 1))
            t += dt.timedelta(minutes=1)
        empty = breaker.Series([])
        series = {k: empty for k in breaker.LIMITS}
        series["no_outcome_share"] = breaker.Series(rows)
        limits = dict(breaker.LIMITS, negative_sentiment_share=(1440, .5, 100))
        return breaker.replay(series, [], limits, T0, T0 + dt.timedelta(hours=6))

    def test_bad_hour_opens_fast_and_recovers(self):
        bad0 = T0 + dt.timedelta(hours=2)
        ch = self.week(bad0, bad0 + dt.timedelta(hours=1))
        first_open = next(t for t, s, _ in ch if s == "open")
        self.assertLessEqual((first_open - bad0).total_seconds() / 60, 15)
        self.assertEqual(ch[-1][1], "closed")
        self.assertIn("half_open", [s for _, s, _ in ch])

    def test_healthy_never_opens(self):
        self.assertEqual(self.week(T0, T0), [])

    def test_unsafe_opens_on_first_case(self):
        empty = breaker.Series([])
        ch = breaker.replay({k: empty for k in breaker.LIMITS}, [T0 + dt.timedelta(minutes=10)],
                            dict(breaker.LIMITS, negative_sentiment_share=(1440, .5, 100)), T0, T0 + dt.timedelta(hours=2))
        self.assertEqual((ch[0][0] - T0).total_seconds() / 60, 10)
        self.assertEqual(ch[0][1], "open")


class Agreement(unittest.TestCase):
    def test_kappa(self):
        self.assertEqual(report.kappa([("y", "y"), ("n", "n")]), 1.0)
        self.assertAlmostEqual(report.kappa([("y", "y"), ("y", "n"), ("n", "n"), ("n", "n")]), .5)


if __name__ == "__main__":
    unittest.main()
