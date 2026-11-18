"""Project 1 rules engine: link accounts, score each claim, decide, log every decision with read-back.

Run: py p1_refund_decisions/engine.py [rules/v1.json]
Pause: set "enabled": false in a NEW rules file and run it; every claim then goes to an agent.
Rollback: run the previous rules file. Rule files are never edited in place (see register()).
"""
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
DB = pathlib.Path(os.environ.get("P1_DB", HERE.parent / "data" / "p1.db"))
DEFAULT_RULES = HERE / "rules" / "v1.json"
WINDOW_START = "2026-08-03"  # claims before this are history only (already decided by agents)
REASONS = {"late", "missing", "wrong_item", "damaged"}
LOG_FIELDS = ("claim_id", "decided_at_utc", "rule_version", "score", "decision", "reason_codes", "is_holdout")


# ---------- account linking ----------
def norm_phone(p):
    return "".join(c for c in p if c.isdigit())[-10:]


def addr_hash(line, pincode):
    return hashlib.md5(f"{' '.join(line.lower().split())}|{pincode}".encode()).hexdigest()


def link(accounts):
    """{account_id: (cluster_id, match_tier, weak_flag)}.

    exact  = same normalised phone or email          -> merge
    strong = same address hash + same pet or card    -> merge
    weak   = same address hash, nothing else matches -> never merge, flag for an agent
    Pincode-only is not used: with real pincodes it links half a city.
    """
    parent = {a["account_id"]: a["account_id"] for a in accounts}
    tier = dict.fromkeys(parent, "none")

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b, t):
        parent[find(a)] = find(b)
        for x in (a, b):
            if tier[x] != "exact":
                tier[x] = t

    keys = collections.defaultdict(list)
    for a in accounts:
        keys["phone", norm_phone(a["phone"])].append(a["account_id"])
        keys["email", a["email"].strip().lower()].append(a["account_id"])
    for ids in keys.values():
        for x in ids[1:]:
            union(x, ids[0], "exact")

    by_addr = collections.defaultdict(list)
    for a in accounts:
        by_addr[addr_hash(a["address_line"], a["pincode"])].append(a)
    pairs = [(a, b) for g in by_addr.values() for i, a in enumerate(g) for b in g[i + 1:]]
    for a, b in pairs:
        if a["pet_name"] == b["pet_name"] or a["pay_fp"] == b["pay_fp"]:
            union(a["account_id"], b["account_id"], "strong")
    weak = {x["account_id"] for a, b in pairs if find(a["account_id"]) != find(b["account_id"]) for x in (a, b)}
    return {aid: (f"C{find(aid)}", tier[aid], int(aid in weak)) for aid in parent}


# ---------- features, score, decision ----------
def ago(ts, days):
    return (dt.datetime.fromisoformat(ts) - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def features(con, view="cluster"):
    """Feature dict per claim in the test window.

    view='account' ignores links: the per-account view agents have today (used to show what linking adds).
    """
    links = {r[0]: r for r in con.execute("SELECT account_id, cluster_id, weak_flag FROM account_links")}
    orders = {r[0]: r for r in con.execute(
        "SELECT order_id, account_id, order_ts_utc, value, promised_date, delivered_ts_utc, delivery_proof FROM orders")}
    approved = {r[0] for r in con.execute("SELECT claim_id FROM agent_decisions WHERE decision = 'approve'")}
    key = (lambda acc: links[acc][1]) if view == "cluster" else (lambda acc: acc)
    ordered = collections.defaultdict(list)
    for _, acc, ts, value, *_ in orders.values():
        ordered[key(acc)].append((ts, value))
    hist = collections.defaultdict(list)  # key -> [(created, amount, refunded)]
    out = {}
    # ponytail: rescans each cluster's history per claim; fine at 10^4 claims, use SQL windows at 10^6
    for cid, oid, acc, amount, reason, t in con.execute(
            "SELECT claim_id, order_id, account_id, amount, reason, created_at_utc FROM claims ORDER BY created_at_utc"):
        k = key(acc)
        if t >= WINDOW_START:
            d90, d180 = ago(t, 90), ago(t, 180)
            _, _, _, value, promised, delivered, proof = orders[oid]
            ordered_rs = sum(v for ts, v in ordered[k] if d180 <= ts < t)
            refunded_rs = sum(a for ts, a, ok in hist[k] if ts >= d180 and ok)
            out[cid] = dict(amount=amount, order_value=value, reason=reason, delivery_proof=proof,
                            delivered_on_time=delivered[:10] <= promised,
                            claims_90d=sum(ts >= d90 for ts, _, _ in hist[k]),
                            refund_rate=refunded_rs / ordered_rs if ordered_rs else 0.0,
                            weak_link=bool(links[acc][2]))
        # ponytail: "refunded" = what today's agents approved; once live, read it from decision outcomes
        hist[k].append((t, amount, cid in approved))
    return out


def score(f, w):
    """Weighted sum of four 0-1 parts. Weights sum to 1, so the score is 0-1."""
    evidence_against = ((f["reason"] == "late" and f["delivered_on_time"])
                        or (f["reason"] == "missing" and f["delivery_proof"] == "otp_ok")
                        or (f["reason"] == "damaged" and f["delivery_proof"] != "photo_ok"))
    parts = dict(claims_90d=min(f["claims_90d"], 4) / 4, refund_rate=min(f["refund_rate"] / .5, 1),
                 evidence_against=float(evidence_against), amount_share=min(f["amount"] / f["order_value"], 1))
    return round(sum(w[k] * v for k, v in parts.items()), 3)


def in_holdout(claim_id, share):
    """Random, repeatable, not gameable: depends only on the claim id."""
    return int(hashlib.md5(str(claim_id).encode()).hexdigest(), 16) % 100 < share * 100


def decide(claim_id, f, cfg):
    """-> (decision, score, reason_codes). decision is approve | decline | agent."""
    s = score(f, cfg["weights"])
    ap = cfg["auto_approve"]
    if in_holdout(claim_id, cfg["holdout_share"]):
        return "agent", s, ["HOLDOUT"]
    if not cfg.get("enabled", True):
        return "agent", s, ["ENGINE_PAUSED"]
    if f["reason"] not in REASONS:
        return "agent", s, ["UNMAPPED_REASON"]
    if f["weak_link"]:  # weak identity matches never decide a claim, in either direction
        return "agent", s, ["WEAK_LINK"]
    for r in cfg["auto_decline"]:  # own amount cap, so the approval limit moves without moving declines
        if (f["reason"], f["delivery_proof"]) == (r["reason"], r["delivery_proof"]) \
                and f["claims_90d"] >= r["min_claims_90d_cluster"] and f["amount"] <= r["max_amount"]:
            return "decline", s, [f"DECLINE_{r['reason']}_{r['delivery_proof']}".upper(), f"CLAIMS_90D_{f['claims_90d']}"]
    if f["amount"] > ap["max_amount"]:
        return "agent", s, ["AMOUNT_ABOVE_LIMIT"]
    if f["claims_90d"] <= ap["max_claims_90d_cluster"] and s < ap["max_score"] \
            and any(all(f[k] == v for k, v in cond.items()) for cond in ap["require_any"]):
        return "approve", s, [f"APPROVE_{f['reason']}".upper()]
    return "agent", s, ["NO_RULE_MATCHED"]


# ---------- storage ----------
def ensure_schema(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS account_links(account_id INT PRIMARY KEY, cluster_id TEXT, match_tier TEXT, weak_flag INT);
    CREATE TABLE IF NOT EXISTS rule_versions(rule_version TEXT PRIMARY KEY, config_json TEXT, approved_by TEXT, approved_at TEXT);
    CREATE TABLE IF NOT EXISTS decision_log(decision_id INTEGER PRIMARY KEY, claim_id INT, decided_at_utc TEXT,
        rule_version TEXT, score REAL, decision TEXT, reason_codes TEXT, is_holdout INT);
    """)


def register(con, cfg):
    """A rule version is immutable: same name + different config is refused."""
    text = json.dumps(cfg, sort_keys=True)
    old = con.execute("SELECT config_json FROM rule_versions WHERE rule_version = ?", (cfg["rule_version"],)).fetchone()
    if old and old[0] != text:
        raise SystemExit(f"{cfg['rule_version']} is already registered with a different config. "
                         "Save the change as a new rules file with a new rule_version.")
    if not old:
        con.execute("INSERT INTO rule_versions VALUES (?,?,?,?)",
                    (cfg["rule_version"], text, cfg["approved_by"], cfg["approved_at"]))


def write_decision(con, row):
    """Insert one log row, read it back, fail loudly if what was stored differs from what was written."""
    cur = con.execute(f"INSERT INTO decision_log ({','.join(LOG_FIELDS)}) VALUES ({','.join('?' * len(LOG_FIELDS))})",
                      [row[k] for k in LOG_FIELDS])
    back = con.execute(f"SELECT {','.join(LOG_FIELDS)} FROM decision_log WHERE decision_id = ?",
                       (cur.lastrowid,)).fetchone()
    if tuple(back) != tuple(row[k] for k in LOG_FIELDS):
        raise RuntimeError(f"read-back mismatch for claim {row['claim_id']}: wrote {row}, read {tuple(back)}")


def run(rules_path=DEFAULT_RULES):
    cfg = json.loads(pathlib.Path(rules_path).read_text())
    con = sqlite3.connect(DB)
    ensure_schema(con)
    register(con, cfg)
    cols = ("account_id", "phone", "email", "address_line", "pincode", "pet_name", "pay_fp")
    accounts = [dict(zip(cols, r)) for r in con.execute(f"SELECT {','.join(cols)} FROM accounts")]
    con.execute("DELETE FROM account_links")
    con.executemany("INSERT INTO account_links VALUES (?,?,?,?)", [(a, *v) for a, v in link(accounts).items()])
    created = dict(con.execute("SELECT claim_id, created_at_utc FROM claims"))
    con.execute("DELETE FROM decision_log WHERE rule_version = ?", (cfg["rule_version"],))  # re-run is idempotent
    counts = collections.Counter()
    for cid, f in features(con).items():
        decision, s, reasons = decide(cid, f, cfg)
        write_decision(con, dict(claim_id=cid, decided_at_utc=created[cid], rule_version=cfg["rule_version"], score=s,
                                 decision=decision, reason_codes=",".join(reasons), is_holdout=int(reasons == ["HOLDOUT"])))
        counts[decision] += 1
    gaps = con.execute("""SELECT COUNT(*) FROM claims c WHERE c.created_at_utc >= ? AND NOT EXISTS
        (SELECT 1 FROM decision_log d WHERE d.claim_id = c.claim_id AND d.rule_version = ?)""",
                       (WINDOW_START, cfg["rule_version"])).fetchone()[0]
    if gaps:
        raise RuntimeError(f"{gaps} claims have no decision_log row")
    con.commit()
    print(f"{cfg['rule_version']}: {dict(counts)}; every row read back OK; no log gaps")
    return cfg


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RULES)
