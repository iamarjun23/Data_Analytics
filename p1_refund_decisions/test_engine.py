"""One fixture per rule branch + linking edge cases + read-back. Stdlib only.

Run: py -m unittest p1_refund_decisions/test_engine.py   (or from this folder: py -m unittest)
"""
import json
import pathlib
import sqlite3
import unittest

from engine import decide, ensure_schema, in_holdout, link, norm_phone, register, write_decision

CFG = json.loads((pathlib.Path(__file__).parent / "rules" / "v1.json").read_text())
NOT_HOLDOUT = next(i for i in range(1, 100) if not in_holdout(i, CFG["holdout_share"]))
HOLDOUT = next(i for i in range(1, 100) if in_holdout(i, CFG["holdout_share"]))


def claim(**kw):
    f = dict(amount=300, order_value=1500, reason="late", delivery_proof="otp_ok", delivered_on_time=False,
             claims_90d=0, refund_rate=0.0, weak_link=False)
    return {**f, **kw}


def acc(aid, phone, addr="1 MG Road", pin="560001", pet="Bruno", pay=None):
    return dict(account_id=aid, phone=phone, email=f"u{aid}@example.com", address_line=addr, pincode=pin,
                pet_name=pet, pay_fp=pay or f"fp{aid}")


def d(f, cid=NOT_HOLDOUT, cfg=CFG):
    return decide(cid, f, cfg)[0]


class Rules(unittest.TestCase):
    def test_approve_late_when_actually_late(self):
        self.assertEqual(d(claim()), "approve")

    def test_late_claim_on_on_time_order_goes_to_agent(self):
        self.assertEqual(d(claim(delivered_on_time=True)), "agent")

    def test_approve_damaged_with_photo(self):
        self.assertEqual(d(claim(reason="damaged", delivery_proof="photo_ok")), "approve")

    def test_damaged_without_photo_goes_to_agent(self):
        self.assertEqual(d(claim(reason="damaged", delivery_proof="none")), "agent")

    def test_decline_missing_with_otp_and_repeat_cluster(self):
        self.assertEqual(d(claim(reason="missing", claims_90d=3)), "decline")
        self.assertEqual(d(claim(reason="missing", claims_90d=2)), "agent")

    def test_weak_link_never_declines(self):
        self.assertEqual(d(claim(reason="missing", claims_90d=5, weak_link=True)), "agent")

    def test_amount_boundary(self):
        self.assertEqual(d(claim(amount=1500)), "approve")
        self.assertEqual(d(claim(amount=1501)), "agent")

    def test_too_many_cluster_claims_blocks_approval(self):
        self.assertEqual(d(claim(claims_90d=2)), "agent")

    def test_unmapped_reason_goes_to_agent(self):
        self.assertEqual(decide(NOT_HOLDOUT, claim(reason="expired"), CFG)[2], ["UNMAPPED_REASON"])

    def test_holdout_skips_rules_and_is_repeatable(self):
        self.assertEqual(decide(HOLDOUT, claim(), CFG)[2], ["HOLDOUT"])
        share = sum(in_holdout(i, .10) for i in range(10000)) / 10000
        self.assertAlmostEqual(share, .10, delta=.01)

    def test_paused_engine_routes_everything(self):
        self.assertEqual(d(claim(), cfg={**CFG, "enabled": False}), "agent")

    def test_score_is_0_to_1(self):
        worst = claim(reason="missing", claims_90d=9, refund_rate=3, amount=5000, order_value=100)
        self.assertEqual(decide(NOT_HOLDOUT, worst, CFG)[1], 1.0)
        self.assertEqual(decide(NOT_HOLDOUT, claim(amount=0), CFG)[1], 0.0)


class Linking(unittest.TestCase):
    def test_phone_normalisation(self):
        self.assertEqual({norm_phone(p) for p in ["+91 98765 43210", "098765 43210", "91-9876543210"]}, {"9876543210"})

    def test_same_phone_typed_differently_is_exact(self):
        out = link([acc(1, "+91 98765 43210", addr="x"), acc(2, "09876543210", addr="y")])
        self.assertEqual(out[1][0], out[2][0])
        self.assertEqual(out[2][1], "exact")

    def test_same_address_and_card_is_strong(self):
        out = link([acc(1, "9000000001", pet="A", pay="card"), acc(2, "9000000002", addr=" 1 mg  road ", pet="B", pay="card")])
        self.assertEqual(out[1][0], out[2][0])
        self.assertEqual(out[1][1], "strong")

    def test_flatmates_sharing_address_are_weak_not_merged(self):
        out = link([acc(1, "9000000001", pet="A"), acc(2, "9000000002", pet="B")])
        self.assertNotEqual(out[1][0], out[2][0])
        self.assertEqual((out[1][2], out[2][2]), (1, 1))


class Storage(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        ensure_schema(self.con)

    def test_read_back(self):
        row = dict(claim_id=7, decided_at_utc="2026-08-03 10:00:00", rule_version="v1", score=.125,
                   decision="approve", reason_codes="APPROVE_LATE", is_holdout=0)
        write_decision(self.con, row)  # raises on mismatch
        self.assertEqual(self.con.execute("SELECT decision FROM decision_log").fetchone()[0], "approve")

    def test_rule_version_is_immutable(self):
        register(self.con, CFG)
        register(self.con, CFG)  # same config: fine
        with self.assertRaises(SystemExit):
            register(self.con, {**CFG, "holdout_share": .2})


if __name__ == "__main__":
    unittest.main()
