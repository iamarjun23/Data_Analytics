"""Conversation quality auditor: redaction, rules-only checks, optional model-assisted judging.

Rules-only (offline, free, deterministic): outcome_recorded, handover_with_context, medicine_safety,
plus rule proxies for resolved / policy_followed / sentiment.
Model mode: Claude scores resolved / policy_followed / sentiment with an evidence quote, via structured
output. Needs `pip install anthropic` and ANTHROPIC_API_KEY. Only redacted text is sent.

audit_scores is append-only: one row per (conversation, rubric_version, mode); a new rubric version
adds new rows, old rows are never rewritten.

Run: py p5_conversation_qa/auditor.py --rubric 1
     py p5_conversation_qa/auditor.py --rubric 2
     py p5_conversation_qa/auditor.py --rubric 2 --mode model --sample 0.05      (needs approval: sends text out)
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import random
import re
import sqlite3

HERE = pathlib.Path(__file__).resolve().parent
DB = HERE.parent / "data" / "p5.db"
FIELDS = ("resolved", "handover_with_context", "policy_followed", "medicine_safety", "sentiment", "outcome_recorded")
MODEL_FIELDS = ("resolved", "policy_followed", "sentiment")
MODEL, PROMPT_VERSION = "claude-opus-5-5", "1"
PRICE = {"in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0}  # USD per million tokens
ORDER_ID = re.compile(r"\bORD\d{6}\b")

# ponytail: regex redaction catches the formats this data uses; a real rollout adds an NER pass for free-text names.
REDACT = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"(?:\+91[\s-]?)?\b[6-9]\d{9}\b"), "[PHONE]"),
    (re.compile(r"\b\d{1,4},?\s+[A-Z0-9][\w ]*?(?:Road|Street|Cross|Main|Layout|Nagar)\b[\w ,]*?(?=\s\d{6}\b|[.]|$)"), "[ADDRESS]"),
    (re.compile(r"\b\d{6}\b"), "[PINCODE]"),
    (re.compile(r"\b(I'm|I am|my name is|this is|Hi|Hello|Dear|Thanks)(,?\s+)([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b"), r"\1\2[NAME]"),
]


def redact(text):
    for rx, token in REDACT:
        text = rx.sub(token, text)
    return text


def load_rubric(version):
    return json.loads((HERE / f"rubric_v{version}.json").read_text(encoding="utf-8"))


def score_rules(conv, ticket, rb):
    """conv: dict(handler, outcome, messages); ticket: dict(status, bot_outcome, handover_summary).
    Returns (scores, notes). Notes carry reasons only, never transcript text."""
    msgs = conv["messages"]
    text = " ".join(m["text"] for m in msgs).lower()
    bot_text = " ".join(m["text"] for m in msgs if m["role"] == "bot").lower()
    staff_text = " ".join(m["text"] for m in msgs if m["role"] != "customer").lower()
    notes, s = [], {}

    s["outcome_recorded"] = "yes" if (ticket["bot_outcome"] if conv["handler"] == "bot" else conv["outcome"]) else "no"

    handed = conv["handler"] == "bot" and (conv["outcome"] == "handed_over" or any(m["role"] == "agent" for m in msgs))
    if not handed:
        s["handover_with_context"] = "not_applicable"
    else:
        summ = ticket["handover_summary"] or ""
        has_ctx = bool(ORDER_ID.search(summ)) or (rb["handover_requires"] == "order_id_or_intent" and "intent=" in summ)
        s["handover_with_context"] = "yes" if has_ctx else "no"
        if not has_ctx:
            notes.append("handover summary empty" if not summ.strip() else "handover summary lacks context")

    if any(re.search(rf"\b{re.escape(k)}\b", text) for k in rb["medicine_keywords"]):
        if any(re.search(p, staff_text) for p in rb["unsafe_patterns"]):
            s["medicine_safety"] = "unsafe"
            notes.append("dosing advice")
        elif not any(p in staff_text for p in rb["disclaimer_phrases"]):
            s["medicine_safety"] = "missing_disclaimer"
            notes.append("no vet-consult line")
        else:
            s["medicine_safety"] = "ok"
    else:
        s["medicine_safety"] = "not_applicable"

    s["policy_followed"] = "no" if any(p in bot_text for p in rb["bot_forbidden_promises"]) else "yes"
    if s["policy_followed"] == "no":
        notes.append("bot promised a refund")

    # rule proxies for the judgement fields; model mode replaces these
    oc = conv["outcome"]
    if oc is None:
        s["resolved"] = "unclear"
    elif oc == "handed_over" or conv["handler"] == "agent":
        s["resolved"] = "yes" if ticket["status"] == "solved" else "no"
    else:
        s["resolved"] = "yes" if oc == "resolved" else "no"
    last = next((m["text"].lower() for m in reversed(msgs) if m["role"] == "customer"), "")
    s["sentiment"] = ("negative" if any(w in last for w in rb["negative_words"])
                      else "positive" if any(w in last for w in rb["positive_words"]) else "neutral")
    return s, "; ".join(notes)


def model_schema():
    def field(values):
        return {"type": "object", "properties": {"value": {"type": "string", "enum": values}, "evidence": {"type": "string"}},
                "required": ["value", "evidence"], "additionalProperties": False}
    return {"type": "object",
            "properties": {"resolved": field(["yes", "no", "unclear"]), "policy_followed": field(["yes", "no"]),
                           "sentiment": field(["positive", "neutral", "negative"])},
            "required": list(MODEL_FIELDS), "additionalProperties": False}


def validate_model_output(d):
    """Belt and braces on top of structured output: exact keys, allowed values, evidence is a string."""
    sch = model_schema()["properties"]
    if not isinstance(d, dict) or set(d) != set(MODEL_FIELDS):
        return False
    return all(isinstance(d[f], dict) and set(d[f]) == {"value", "evidence"} and isinstance(d[f]["evidence"], str)
               and d[f]["value"] in sch[f]["properties"]["value"]["enum"] for f in MODEL_FIELDS)


class ModelJudge:
    def __init__(self, rb):
        import anthropic  # only needed in model mode
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        self.cap = float(os.environ.get("AUDIT_DAILY_CAP_USD", "5"))
        self.spent = 0.0
        tmpl = (HERE / f"prompt_v{PROMPT_VERSION}.md").read_text(encoding="utf-8")
        self.system = tmpl.format(policy=rb["policy_text"], version=rb["version"],
                                  rubric=json.dumps({f: rb["fields"][f] for f in MODEL_FIELDS}, indent=1))

    def score(self, redacted_transcript):
        if self.spent >= self.cap:
            raise RuntimeError(f"ALERT daily model spend cap reached (${self.cap:.2f}); stopping model scoring")
        for _ in range(2):  # one retry on a malformed or refused answer
            r = self.client.beta.messages.create(
                model=MODEL, max_tokens=2000,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",  # re-run on a fallback model if declined
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],  # static rubric+policy cached
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": model_schema()}},
                messages=[{"role": "user", "content": redacted_transcript}])
            u = r.usage
            self.spent += ((u.input_tokens * PRICE["in"] + u.output_tokens * PRICE["out"]
                            + (u.cache_read_input_tokens or 0) * PRICE["cache_read"]
                            + (u.cache_creation_input_tokens or 0) * PRICE["cache_write"]) / 1e6)
            if r.stop_reason == "refusal":
                continue
            try:
                d = json.loads(next(b.text for b in r.content if b.type == "text"))
            except (StopIteration, json.JSONDecodeError):
                continue
            if validate_model_output(d):
                return d
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rubric", type=int, default=1)
    ap.add_argument("--mode", choices=["rules", "model"], default="rules")
    ap.add_argument("--sample", type=float, default=1.0, help="share of conversations to score (0.05 = 5%%)")
    ap.add_argument("--flow", help="score 100%% of this flow (flows under review)")
    a = ap.parse_args()
    rb = load_rubric(a.rubric)
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS audit_scores(conversation_id TEXT, rubric_version INT, mode TEXT, prompt_version TEXT,
        resolved TEXT, handover_with_context TEXT, policy_followed TEXT, medicine_safety TEXT, sentiment TEXT,
        outcome_recorded TEXT, notes TEXT, scored_at_utc TEXT, PRIMARY KEY (conversation_id, rubric_version, mode));
    """)
    rows = con.execute("""SELECT t.conversation_id, t.handler, t.flow, t.outcome, t.messages_json,
                                 k.status, k.bot_outcome, k.handover_summary
                          FROM transcripts t JOIN tickets k USING (conversation_id)
                          WHERE NOT EXISTS (SELECT 1 FROM audit_scores a WHERE a.conversation_id = t.conversation_id
                                            AND a.rubric_version = ? AND a.mode = ?)
                          ORDER BY t.conversation_id""", (rb["version"], a.mode)).fetchall()
    rnd = random.Random(f"{dt.date.today()}-{a.rubric}")  # a fresh, reproducible daily sample
    rows = [r for r in rows if (a.flow and r[2] == a.flow) or (not a.flow and rnd.random() < a.sample)]
    judge = ModelJudge(rb) if a.mode == "model" else None
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for cid, handler, flow, outcome, mj, status, bot_outcome, summ in rows:
        conv = {"handler": handler, "outcome": outcome, "messages": json.loads(mj)}
        s, notes = score_rules(conv, {"status": status, "bot_outcome": bot_outcome, "handover_summary": summ}, rb)
        if judge:
            red = "\n".join(f"{m['role']}: {redact(m['text'])}" for m in conv["messages"])
            red += f"\n[ticket handover summary]: {redact(summ or '')}"
            try:
                d = judge.score(red)
            except RuntimeError as e:
                print(e)
                break
            if d is None:
                s.update(resolved="unclear")
                notes = (notes + "; model output rejected twice").strip("; ")
            else:
                s.update({f: d[f]["value"] for f in MODEL_FIELDS})
                ev = " | ".join(f"{f}: \"{d[f]['evidence'][:120]}\"" for f in MODEL_FIELDS if d[f]["evidence"])
                notes = (notes + "; " + ev).strip("; ")  # evidence is quoted from redacted text only
        out.append((cid, rb["version"], a.mode, PROMPT_VERSION if judge else None, *(s[f] for f in FIELDS), notes, now))
    con.executemany("INSERT OR IGNORE INTO audit_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", out)
    con.commit()
    msg = f"scored={len(out)} rubric=v{rb['version']} mode={a.mode}"
    print(msg + (f" spend=${judge.spent:.2f}" if judge else ""))


if __name__ == "__main__":
    main()
