"""Customer crosswalk, coverage report, customer_360 and clinic duplicate-booking detector.
Run: py p4_customer_profile/build_profile.py [--force]  ->  tables in data/p4.db + coverage_report.md

Deterministic tiers, no ML. A record's tier says how it reaches its customer's anchor account:
  A  shared key (order->account, ticket->order, rx->account) or same normalised phone   high
  B  same email                                                                          high
  C  same normalised address and same pet name                                           medium
  D  same pincode, pet name and first name -> match_candidates only, never merged        low
Guards: a phone that is nobody's account phone but would join two accounts is "shared" and never
links; a cluster with more than 5 accounts or 5 phones goes to cluster_review, not accepted.
"""
import collections
import datetime as dt
import hashlib
import itertools
import pathlib
import re
import sqlite3
import statistics
import sys

from gen_identity_data import AS_OF, DB

HERE = pathlib.Path(__file__).resolve().parent
MAX_ACCOUNTS = MAX_PHONES = 5
COVERAGE_DROP_HOLD = 3.0  # points per source; runbook: hold the release


def norm_phone(p):
    d = re.sub(r"\D", "", p or "")
    return d[-10:] if len(d) >= 10 else None


def norm_email(e):
    return (e or "").strip().lower() or None


def norm_addr(a):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).split()) or None


def clean(s):
    return (s or "").strip() or None


def hid(s):
    return hashlib.sha256(s.encode()).hexdigest()[:12]


# source -> id, phone, email, address, pet, pincode, first_name, created_at, reference to another record
SOURCES = {
    "accounts": "SELECT account_id, phone, email, address, pet_name, pincode, first_name, created_at, NULL FROM accounts",
    "orders": "SELECT order_id, ship_phone, NULL, NULL, NULL, NULL, NULL, NULL, 'accounts|' || account_id FROM orders",
    "tickets": "SELECT ticket_id, phone, NULL, NULL, NULL, NULL, NULL, NULL, 'orders|' || order_id FROM tickets",
    "calls": "SELECT call_id, phone, NULL, NULL, NULL, NULL, NULL, NULL, NULL FROM calls",
    "pharmacy_rx": "SELECT rx_id, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'accounts|' || account_id FROM pharmacy_rx",
    "vet_consults": "SELECT consult_id, phone, NULL, NULL, NULL, NULL, NULL, NULL, NULL FROM vet_consults",
    "clinic_bookings": "SELECT booking_id, phone, NULL, NULL, NULL, NULL, NULL, NULL, NULL FROM clinic_bookings",
}


def why_unmatched(r, shared):
    if r["phone"] in shared:
        return "shared phone, not used to link"
    if r["ref"]:
        return "key points to a record with no account"
    if r["src"] == "tickets":
        return "no order id, phone not on any account"
    return "phone not on any account" if r["phone"] else "no usable phone"


def match(con):
    recs = []
    for src, q in SOURCES.items():
        for sid, ph, em, ad, pet, pin, fn, created, ref in con.execute(q + " ORDER BY 1"):
            recs.append(dict(src=src, sid=sid, phone=norm_phone(ph), email=norm_email(em), addr=norm_addr(ad),
                             pet=(clean(pet) or "").lower() or None, pin=clean(pin), fn=clean(fn), created=created,
                             ref=tuple(ref.split("|", 1)) if ref else None))
    n = len(recs)
    idx = {(r["src"], r["sid"]): k for k, r in enumerate(recs)}
    acct = [k for k, r in enumerate(recs) if r["src"] == "accounts"]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def groups(key):
        g = collections.defaultdict(list)
        for k, r in enumerate(recs):
            if (v := key(r)) is not None:
                g[v].append(k)
        return g

    for k, r in enumerate(recs):  # tier A, part 1: foreign keys
        if r["ref"] in idx:
            parent[find(k)] = find(idx[r["ref"]])

    # Tier A, part 2: same phone. Account phones link first. A number that is nobody's account
    # phone but would still join two accounts (clinic desk, office reception) is shared: never links.
    own = {recs[k]["phone"] for k in acct}
    by_phone = groups(lambda r: r["phone"])
    for p in own & by_phone.keys():
        for k in by_phone[p][1:]:
            parent[find(k)] = find(by_phone[p][0])
    with_acct = {find(k) for k in acct}
    shared = {p: len(ks) for p, ks in by_phone.items()
              if p not in own and len({find(k) for k in ks} & with_acct) > 1}

    snap = {}
    for tier, gs in (("A", {p: ks for p, ks in by_phone.items() if p not in shared and p not in own}),
                     ("B", groups(lambda r: r["email"])),
                     ("C", groups(lambda r: (r["addr"], r["pet"]) if r["addr"] and r["pet"] else None))):
        for ks in gs.values():
            for k in ks[1:]:
                parent[find(k)] = find(ks[0])
        snap[tier] = [find(k) for k in range(n)]

    comps = collections.defaultdict(list)
    for k in range(n):
        comps[snap["C"][k]].append(k)

    xwalk, unmatched, review, lookup, cust_of = [], [], [], [], {}
    for ks in comps.values():
        accs = [k for k in ks if recs[k]["src"] == "accounts"]
        phones = sorted({recs[k]["phone"] for k in ks} - {None})
        if not accs:
            unmatched += [(recs[k]["src"], recs[k]["sid"], why_unmatched(recs[k], shared)) for k in ks]
            continue
        if len(accs) > MAX_ACCOUNTS or len(phones) > MAX_PHONES:
            review.append((f"cl_{hid(min(recs[k]['sid'] for k in accs))}", len(accs), len(phones), len(ks), "pending"))
            unmatched += [(recs[k]["src"], recs[k]["sid"], "cluster flagged for review") for k in ks]
            continue
        anchor = min(accs, key=lambda k: (recs[k]["created"], recs[k]["sid"]))
        cid = "cu_" + hid(recs[anchor]["sid"])  # stable: same anchor account -> same id on every run
        for k in ks:
            tier = next(t for t in "ABC" if snap[t][k] == snap[t][anchor])
            xwalk.append((cid, recs[k]["src"], recs[k]["sid"], tier))
            cust_of[k] = cid
        a = recs[anchor]
        lookup += [(cid, p, int(p == a["phone"]), a["fn"]) for p in phones]

    cands = set()
    tier_d = groups(lambda r: (r["pin"], r["pet"], r["fn"].lower())
                    if r["src"] == "accounts" and r["pin"] and r["pet"] and r["fn"] else None)
    for ks in tier_d.values():
        for i, j in itertools.combinations([k for k in ks if k in cust_of], 2):
            if cust_of[i] != cust_of[j]:
                cands.add((*sorted((cust_of[i], cust_of[j])), "D", "pincode+pet_name+first_name", "pending"))

    # Score against the planted answer key (synthetic data only): pairs of accounts merged.
    truth = dict(con.execute("SELECT source_id, person_id FROM truth_person WHERE source = 'accounts'"))
    score = None
    if truth:
        def pairs(labels):
            return sum(c * (c - 1) // 2 for c in collections.Counter(labels).values())
        got = [cust_of.get(k, recs[k]["sid"]) for k in acct]
        tru = [truth[recs[k]["sid"]] for k in acct]
        tp = pairs(zip(got, tru))
        score = dict(precision=tp / max(pairs(got), 1), recall=tp / max(pairs(tru), 1),
                     missed=pairs(tru) - tp, wrong=pairs(got) - tp)
    return dict(xwalk=xwalk, unmatched=unmatched, review=review, lookup=lookup,
                shared=sorted(shared.items()), cands=sorted(cands), score=score)


CUSTOMER_360 = """
CREATE TABLE customer_360 AS
WITH x AS (SELECT * FROM customer_xwalk),
acc AS (SELECT x.customer_id, COUNT(*) n_accounts, MIN(a.created_at) first_seen
        FROM x JOIN accounts a ON x.source = 'accounts' AND a.account_id = x.source_id GROUP BY 1),
ph AS (SELECT customer_id, COUNT(*) n_phones FROM contact_lookup GROUP BY 1),
o AS (SELECT x.customer_id, COUNT(*) order_count, MAX(o.order_date) last_order_date, ROUND(AVG(o.value)) aov
      FROM x JOIN orders o ON x.source = 'orders' AND o.order_id = x.source_id GROUP BY 1),
t AS (SELECT x.customer_id, SUM(t.created_at >= date(:as_of, '-90 day')) tickets_90d, MAX(t.created_at) last_ticket
      FROM x JOIN tickets t ON x.source = 'tickets' AND t.ticket_id = x.source_id GROUP BY 1),
cs AS (SELECT customer_id, csat FROM (
         SELECT x.customer_id, t.csat, ROW_NUMBER() OVER (PARTITION BY x.customer_id ORDER BY t.created_at DESC) rn
         FROM x JOIN tickets t ON x.source = 'tickets' AND t.ticket_id = x.source_id WHERE t.csat IS NOT NULL)
       WHERE rn = 1),
c AS (SELECT x.customer_id, SUM(c.start_ts >= date(:as_of, '-90 day')) calls_90d, MAX(substr(c.start_ts, 1, 10)) last_call
      FROM x JOIN calls c ON x.source = 'calls' AND c.call_id = x.source_id GROUP BY 1),
rx AS (SELECT x.customer_id, MIN(date(r.last_fill_date, '+' || r.refill_days || ' day')) next_refill_due
       FROM x JOIN pharmacy_rx r ON x.source = 'pharmacy_rx' AND r.rx_id = x.source_id GROUP BY 1),
vc AS (SELECT x.customer_id, SUM(v.consult_date >= date(:as_of, '-365 day')) consults_12m
       FROM x JOIN vet_consults v ON x.source = 'vet_consults' AND v.consult_id = x.source_id GROUP BY 1),
cb AS (SELECT x.customer_id, SUM(b.status = 'completed' AND b.slot_ts >= date(:as_of, '-365 day')) clinic_visits_12m
       FROM x JOIN clinic_bookings b ON x.source = 'clinic_bookings' AND b.booking_id = x.source_id GROUP BY 1),
cn AS (SELECT l.customer_id,  -- consent on the primary number; 'revoked' > 'granted', so MAX is the cautious pick
              MAX(CASE WHEN l.is_primary AND n.channel = 'call' THEN n.consent_status END) consent_call,
              MAX(CASE WHEN l.is_primary AND n.channel = 'whatsapp' THEN n.consent_status END) consent_whatsapp,
              MAX(CASE WHEN l.is_primary AND n.channel = 'sms' THEN n.consent_status END) consent_sms,
              MAX(n.dnd_flag) dnd_flag  -- DND on any of the customer's numbers counts
       FROM contact_lookup l JOIN consent_norm n USING (phone_norm) GROUP BY 1),
q AS (SELECT customer_id,
             'A:' || SUM(match_tier = 'A') || ' B:' || SUM(match_tier = 'B') || ' C:' || SUM(match_tier = 'C') match_tier_mix,
             MAX(CASE WHEN source = 'accounts' THEN match_tier END) match_quality
      FROM x GROUP BY 1)
SELECT acc.customer_id, ph.n_phones, acc.n_accounts, acc.first_seen,
       COALESCE(o.order_count, 0) order_count, o.last_order_date, o.aov, g.usual_gap_days,
       COALESCE(t.tickets_90d, 0) tickets_90d, COALESCE(c.calls_90d, 0) calls_90d,
       NULLIF(MAX(COALESCE(substr(t.last_ticket, 1, 10), ''), COALESCE(c.last_call, '')), '') last_contact_date,
       cs.csat last_csat,
       rx.customer_id IS NOT NULL has_chronic_medication, rx.next_refill_due,
       COALESCE(vc.consults_12m, 0) consults_12m, COALESCE(cb.clinic_visits_12m, 0) clinic_visits_12m,
       COALESCE(cn.consent_call, 'unknown') consent_call, COALESCE(cn.consent_whatsapp, 'unknown') consent_whatsapp,
       COALESCE(cn.consent_sms, 'unknown') consent_sms, COALESCE(cn.dnd_flag, 0) dnd_flag,
       rx.customer_id IS NOT NULL OR vc.customer_id IS NOT NULL sensitivity_flag,
       CASE WHEN cn.dnd_flag = 1 THEN 'dnd'
            WHEN 'granted' NOT IN (COALESCE(cn.consent_call, ''), COALESCE(cn.consent_whatsapp, ''), COALESCE(cn.consent_sms, ''))
            THEN 'no_consent' END do_not_contact_reason,
       q.match_tier_mix, q.match_quality
FROM acc JOIN q USING (customer_id)
LEFT JOIN ph USING (customer_id) LEFT JOIN o USING (customer_id) LEFT JOIN usual_gap g USING (customer_id)
LEFT JOIN t USING (customer_id) LEFT JOIN cs USING (customer_id) LEFT JOIN c USING (customer_id)
LEFT JOIN rx USING (customer_id) LEFT JOIN vc USING (customer_id) LEFT JOIN cb USING (customer_id)
LEFT JOIN cn USING (customer_id)
"""

# Same customer, same pet, same specialty, same week, more than one active booking.
CLINIC_DUPLICATES = """
CREATE TABLE clinic_duplicates AS
SELECT x.customer_id, lower(trim(b.pet_name)) pet, b.specialty, strftime('%Y-W%W', b.slot_ts) week,
       COUNT(*) n_bookings, COUNT(DISTINCT b.clinic_id) n_clinics, group_concat(b.booking_id) booking_ids,
       MAX(x.match_tier) worst_tier, 'agent_confirm' action  -- flagged, never auto-cancelled
FROM clinic_bookings b JOIN customer_xwalk x ON x.source = 'clinic_bookings' AND x.source_id = b.booking_id
WHERE b.status = 'active'
GROUP BY 1, 2, 3, 4 HAVING COUNT(*) > 1
"""

COVERAGE = """
WITH s AS (SELECT source, source_id FROM customer_xwalk UNION ALL SELECT source, source_id FROM unmatched_records)
SELECT s.source, COUNT(*) records, COUNT(x.customer_id) matched,
       ROUND(100.0 * COUNT(x.customer_id) / COUNT(*), 1) matched_pct,
       COUNT(*) FILTER (WHERE x.match_tier = 'C') medium_confidence
FROM s LEFT JOIN customer_xwalk x USING (source, source_id)
GROUP BY 1 ORDER BY matched_pct
"""


def write(con, m, built_at):
    for t in ("customer_xwalk", "unmatched_records", "cluster_review", "shared_phones", "match_candidates",
              "contact_lookup", "consent_norm", "usual_gap", "customer_360", "clinic_duplicates"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    for ddl in """
      CREATE TABLE customer_xwalk(customer_id TEXT NOT NULL, source TEXT, source_id TEXT, match_tier TEXT, linked_at TEXT,
                                  PRIMARY KEY (source, source_id));  -- a record belongs to one customer only
      CREATE TABLE unmatched_records(source TEXT, source_id TEXT, reason TEXT);
      CREATE TABLE cluster_review(cluster_key TEXT, n_accounts INT, n_phones INT, n_records INT, status TEXT);
      CREATE TABLE shared_phones(phone_hash TEXT, n_records INT);
      CREATE TABLE match_candidates(customer_a TEXT, customer_b TEXT, match_tier TEXT, rule TEXT, status TEXT);
      CREATE TABLE contact_lookup(customer_id TEXT, phone_norm TEXT, is_primary INT, first_name TEXT);  -- list job only
      CREATE TABLE consent_norm(phone_norm TEXT, channel TEXT, consent_status TEXT, dnd_flag INT);
      CREATE TABLE usual_gap(customer_id TEXT, usual_gap_days REAL)
    """.split(";"):  # not executescript: it would COMMIT the open transaction
        con.execute(ddl)
    con.executemany("INSERT INTO customer_xwalk VALUES (?, ?, ?, ?, ?)", [(*r, built_at) for r in m["xwalk"]])
    con.executemany("INSERT INTO unmatched_records VALUES (?, ?, ?)", m["unmatched"])
    con.executemany("INSERT INTO cluster_review VALUES (?, ?, ?, ?, ?)", m["review"])
    con.executemany("INSERT INTO shared_phones VALUES (?, ?)", [(hid(p), c) for p, c in m["shared"]])
    con.executemany("INSERT INTO match_candidates VALUES (?, ?, ?, ?, ?)", m["cands"])
    con.executemany("INSERT INTO contact_lookup VALUES (?, ?, ?, ?)", m["lookup"])
    con.executemany("INSERT INTO consent_norm VALUES (?, ?, ?, ?)",
                    [(norm_phone(p), *rest) for p, *rest in con.execute(
                        "SELECT phone, channel, consent_status, dnd_flag FROM consent").fetchall()])
    gaps = collections.defaultdict(list)  # usual gap between non-pharmacy orders: LAG in SQL, median here
    for cid, g in con.execute("""
            SELECT x.customer_id, julianday(o.order_date)
                   - julianday(LAG(o.order_date) OVER (PARTITION BY x.customer_id ORDER BY o.order_date))
            FROM customer_xwalk x JOIN orders o ON x.source = 'orders' AND o.order_id = x.source_id
            WHERE o.category != 'pharmacy'""").fetchall():
        if g is not None:
            gaps[cid].append(g)
    con.executemany("INSERT INTO usual_gap VALUES (?, ?)",
                    [(c, statistics.median(v)) for c, v in gaps.items() if len(v) >= 2])
    con.execute(CUSTOMER_360, {"as_of": str(AS_OF)})
    con.execute(CLINIC_DUPLICATES)


def build(con, force=False):
    """Rebuild every profile table in one transaction. Returns (match result, alerts); held builds roll back."""
    con.isolation_level = None
    con.execute("CREATE TABLE IF NOT EXISTS coverage_history(built_at TEXT, source TEXT, matched_pct REAL)")
    m = match(con)
    built_at = dt.datetime.now().isoformat(timespec="seconds")
    con.execute("BEGIN")
    write(con, m, built_at)
    cov = con.execute(COVERAGE).fetchall()
    prev = dict(con.execute("SELECT source, matched_pct FROM coverage_history "
                            "WHERE built_at = (SELECT MAX(built_at) FROM coverage_history)"))
    alerts = [f"HOLD: {s} coverage {prev[s]}% -> {pct}%" for s, _, _, pct, _ in cov
              if s in prev and prev[s] - pct > COVERAGE_DROP_HOLD]
    if alerts and not force:
        con.execute("ROLLBACK")
        return m, alerts
    con.executemany("INSERT INTO coverage_history VALUES (?, ?, ?)", [(built_at, s, pct) for s, _, _, pct, _ in cov])
    con.execute("COMMIT")
    return m, alerts


TIER_C_CUSTOMERS = "SELECT COUNT(*) FROM customer_360 WHERE match_quality = 'C'"


def report(con, m):
    one = lambda q: con.execute(q).fetchone()[0]
    reasons = collections.defaultdict(list)
    for s, why, c in con.execute("SELECT source, reason, COUNT(*) FROM unmatched_records GROUP BY 1, 2 ORDER BY 3 DESC"):
        reasons[s].append(f"{why} ({c})")
    out = [f"# Coverage report (P4 customer profile, data as of {AS_OF})", "",
           "SYNTHETIC data. Published with every build of the profile.", "",
           "| Source | Records | Matched | Matched % | Medium-confidence (tier C) | Unmatched reason |",
           "| --- | ---: | ---: | ---: | ---: | --- |"]
    out += [f"| {s} | {n:,} | {mt:,} | {pct}% | {c} | {'; '.join(reasons[s]) or '-'} |"
            for s, n, mt, pct, c in con.execute(COVERAGE)]
    out += ["", "| Check | Value |", "| --- | ---: |",
            f"| Customers | {one('SELECT COUNT(*) FROM customer_360'):,} |",
            f"| Customers with more than one phone | {one('SELECT COUNT(*) FROM customer_360 WHERE n_phones > 1'):,} |",
            f"| Customers with more than one account | {one('SELECT COUNT(*) FROM customer_360 WHERE n_accounts > 1'):,} |",
            f"| Customers whose accounts were joined only by tier C | {one(TIER_C_CUSTOMERS)} |",
            f"| Clusters flagged for review (> {MAX_ACCOUNTS} accounts or phones) | {len(m['review'])} |",
            f"| Shared phones not used to link | {len(m['shared'])} |",
            f"| Tier D candidates waiting for review | {len(m['cands'])} |"]
    if m["score"]:
        s = m["score"]
        out += [f"| Account-pair precision vs planted truth | {s['precision']:.1%} |",
                f"| Account-pair recall vs planted truth | {s['recall']:.1%} ({s['missed']} same-person pairs missed) |"]

    truth = dict(con.execute("SELECT source_id, person_id FROM truth_person WHERE source = 'clinic_bookings'"))
    dups = con.execute("SELECT week, n_bookings, booking_ids FROM clinic_duplicates ORDER BY week").fetchall()
    wrong = sum(len({truth.get(b) for b in ids.split(",")}) > 1 for _, _, ids in dups) if truth else "n/a"
    out += ["", "## Clinic duplicate bookings", "",
            "Active bookings for the same customer, pet and specialty in the same week. Every group goes to an",
            "agent to confirm; nothing is cancelled automatically.", "",
            "| Week | Duplicate groups | Extra slots that could be freed |", "| --- | ---: | ---: |"]
    by_week = collections.defaultdict(lambda: [0, 0])
    for week, nb, _ in dups:
        by_week[week][0] += 1
        by_week[week][1] += nb - 1
    out += [f"| {w} | {g} | {s} |" for w, (g, s) in sorted(by_week.items())]
    out += ["", f"Groups that are really two different people (would be wrongly contacted): **{wrong}**."]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    con = sqlite3.connect(DB)
    m, alerts = build(con, force="--force" in sys.argv)
    for a in alerts:
        print(a)
    if alerts and "--force" not in sys.argv:
        sys.exit("Build held; previous profile kept. Find the source change, or rerun with --force.")
    (HERE / "coverage_report.md").write_text(report(con, m), encoding="utf-8")
    print((HERE / "coverage_report.md").read_text(encoding="utf-8"))
