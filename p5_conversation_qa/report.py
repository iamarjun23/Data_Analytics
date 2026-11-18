"""Daily quality table, flow handover ranking, safety list, auditor-vs-human agreement, write-back and breaker summary.
Run: py p5_conversation_qa/report.py [--rubric 2]  ->  p5_conversation_qa/report.md
Prints ids and labels only, never transcript text.
"""
import argparse
import collections
import pathlib
import sqlite3

HERE = pathlib.Path(__file__).resolve().parent
DB = HERE.parent / "data" / "p5.db"
FIELDS = ("resolved", "handover_with_context", "policy_followed", "medicine_safety", "sentiment", "outcome_recorded")
IST_DATE = "date(t.started_at_utc, '+5 hours', '+30 minutes')"


def kappa(pairs):
    """Cohen's kappa for a list of (a, b) labels."""
    n = len(pairs)
    if not n:
        return float("nan")
    po = sum(a == b for a, b in pairs) / n
    ca, cb = collections.Counter(a for a, _ in pairs), collections.Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in ca) / n / n
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return "\n".join(out + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def pct(n, d):
    return f"{100 * n / d:.1f}%" if d else "-"


QUALITY = f"""
SELECT {{key}}, COUNT(*) n,
  SUM(a.resolved = 'yes'), SUM(a.resolved != 'unclear'),
  SUM(a.handover_with_context = 'yes'), SUM(a.handover_with_context != 'not_applicable'),
  SUM(a.policy_followed = 'yes'),
  SUM(a.medicine_safety IN ('missing_disclaimer', 'unsafe')),
  SUM(a.sentiment = 'negative'), SUM(a.outcome_recorded = 'yes')
FROM transcripts t JOIN audit_scores a USING (conversation_id)
WHERE a.rubric_version = ? AND a.mode = 'rules' GROUP BY {{key}} ORDER BY {{key}}"""
QHEAD = ["conversations", "resolved", "handover w/ context", "policy followed", "medicine issues", "negative", "outcome recorded"]


def qrows(con, key, rb):
    return [(*r[:-9], r[-9], pct(r[-8], r[-7]), pct(r[-6], r[-5]), pct(r[-4], r[-9]), r[-3], pct(r[-2], r[-9]), pct(r[-1], r[-9]))
            for r in con.execute(QUALITY.format(key=key), (rb,))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rubric", type=int, default=2)
    rb = ap.parse_args().rubric
    con = sqlite3.connect(DB)
    md = [f"# Conversation quality report (rubric v{rb}, rules mode)",
          "SYNTHETIC data, 1-7 Nov 2026. Dates in IST. Rates: resolved excludes 'unclear'; handover-with-context is over handovers.", ""]

    md += ["## Daily quality table", table(["date (IST)"] + QHEAD, qrows(con, IST_DATE, rb)), ""]
    md += ["## By flow and handler (week)", table(["flow", "handler"] + QHEAD, qrows(con, "t.flow, t.handler", rb)), ""]

    flows = con.execute("""
        SELECT flow, COUNT(*) n, SUM(outcome = 'handed_over') h,
               (SELECT group_concat(conversation_id, ', ') FROM (SELECT conversation_id FROM transcripts x
                 WHERE x.flow = t.flow AND x.outcome = 'handed_over' ORDER BY conversation_id LIMIT 3)) sample
        FROM transcripts t WHERE handler = 'bot' GROUP BY flow ORDER BY 1.0 * h / n DESC""").fetchall()
    md += ["## Flows with most handovers (bot sessions)",
           table(["flow", "bot sessions", "handed over", "handover rate", "sample ids for the owner"],
                 [(f, n, h, pct(h, n), s) for f, n, h, s in flows]), ""]

    safety = con.execute(f"""
        SELECT a.conversation_id, {IST_DATE}, time(t.started_at_utc, '+5 hours', '+30 minutes'), t.flow, a.medicine_safety, a.notes
        FROM audit_scores a JOIN transcripts t USING (conversation_id)
        WHERE a.rubric_version = ? AND a.mode = 'rules' AND a.medicine_safety IN ('unsafe', 'missing_disclaimer')
        ORDER BY a.medicine_safety DESC, t.started_at_utc""", (rb,)).fetchall()
    md += [f"## Safety list ({len(safety)}) - send to the pharmacy owner the same day",
           table(["conversation", "date", "time IST", "flow", "status", "rule note"], safety), ""]

    md += ["## Auditor vs human labels", "Human sample: 100 conversations (40 random, 30 pharmacy, 30 handovers).", ""]
    rows, worst = [], []
    for ver, mode in con.execute("SELECT DISTINCT rubric_version, mode FROM audit_scores ORDER BY 1, 2"):
        data = con.execute(f"""SELECT h.conversation_id, {", ".join(f"a.{f}, h.{f}" for f in FIELDS)}
            FROM human_labels h JOIN audit_scores a USING (conversation_id)
            WHERE a.rubric_version = ? AND a.mode = ?""", (ver, mode)).fetchall()
        for i, f in enumerate(FIELDS):
            pairs = [(r[1 + 2 * i], r[2 + 2 * i]) for r in data]
            rows.append((f"v{ver} {mode}", f, len(pairs), pct(sum(a == b for a, b in pairs), len(pairs)), f"{kappa(pairs):.2f}"))
            if ver == 1:
                worst += [(r[0], f, r[1 + 2 * i], r[2 + 2 * i]) for r in data if r[1 + 2 * i] != r[2 + 2 * i]]
    md += [table(["rubric / mode", "field", "n", "agreement", "Cohen's kappa"], rows), ""]
    by = collections.Counter((f, a, h) for _, f, a, h in worst)
    md += ["### v1 disagreements (what drove rubric v2)",
           table(["field", "auditor said", "human said", "count", "example ids"],
                 [(f, a, h, c, ", ".join(x[0] for x in worst if x[1:] == (f, a, h))[:60]) for (f, a, h), c in by.most_common()]), ""]

    n_ev, ok, parked = con.execute("""SELECT COUNT(*), SUM(ok), SUM(1 - ok) FROM
        (SELECT event_id, MAX(stored_ok) ok FROM webhook_log GROUP BY event_id)""").fetchone()
    retries_saved = con.execute("""SELECT COUNT(*) FROM (SELECT event_id FROM webhook_log GROUP BY event_id
        HAVING MAX(stored_ok) = 1 AND MAX(attempt) > 1)""").fetchone()[0]
    mism = con.execute("SELECT COUNT(*) FROM webhook_log WHERE note LIKE 'mismatch%'").fetchone()[0]
    md += ["## Write-back (webhook -> ticket fields)",
           f"- Events: {n_ev}. Stored and read back matching: {ok} ({pct(ok, n_ev)}). Parked after retries: {parked}.",
           f"- Recovered by retry: {retries_saved}. Read-back mismatches: {mism}.", ""]

    changes = con.execute("SELECT state, changed_at_utc, reason FROM breaker_state ORDER BY changed_at_utc").fetchall()
    md += ["## Circuit breaker (replay of the week)",
           table(["state", "changed at (UTC)", "reason"], changes), ""]

    # weekly summary: top problems by count
    probs = con.execute("""SELECT
        SUM(handover_with_context = 'no'), SUM(outcome_recorded = 'no'), SUM(policy_followed = 'no'),
        SUM(medicine_safety IN ('unsafe', 'missing_disclaimer')), SUM(sentiment = 'negative')
        FROM audit_scores WHERE rubric_version = ? AND mode = 'rules'""", (rb,)).fetchone()
    names = ["handovers without context", "sessions with no outcome in the ticket", "refund-policy breaches by the bot",
             "medicine answers missing the vet line or unsafe", "negative endings"]
    top = sorted(zip(probs, names), reverse=True)[:3]
    md += ["## Weekly summary",
           "Top problems by count: " + "; ".join(f"{n} ({c})" for c, n in top) + ".",
           "Recommendation: see README results section (owner: Product + bot vendor).", ""]

    (HERE / "report.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
