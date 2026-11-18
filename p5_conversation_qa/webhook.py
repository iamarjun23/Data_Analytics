"""Write-back of bot outcomes into ticket fields: payload spec, send, read back, retry, park, alert.

The ticket system is a local fake (FakeTicketAPI) over the `tickets` table so the whole flow runs offline.
Swap it for the vendor client in production; send_with_readback() does not change.
Run: py p5_conversation_qa/webhook.py   (replays every bot session's write-back into data/p5.db)
"""
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import uuid

DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "p5.db"
SCHEMA_VERSION = "1"
OUTCOMES = {"resolved", "handed_over", "abandoned"}
MAX_SUMMARY = 1000          # vendor field limit; we cut before sending so read-back can match
BACKOFF_MIN = [0, 1, 2, 4]  # first try + 3 retries, minutes after the session ends
WEBHOOK_DOWN = (dt.datetime(2026, 11, 3, 9, 0), dt.datetime(2026, 11, 3, 9, 40))  # planted vendor outage
ALERT_FAIL_RATE = .02       # alert when >2% of events in a rolling hour end parked
FIELDS = ("bot_outcome", "handover_flag", "handover_summary")


def build_payload(conversation_id, ticket_id, outcome, summary):
    return {
        "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"p5/{conversation_id}")),  # same session -> same id, retries are idempotent
        "conversation_id": conversation_id,
        "ticket_id": ticket_id,
        "bot_outcome": outcome,
        "handover_flag": outcome == "handed_over" or summary is not None,
        "handover_summary": (summary or "")[:MAX_SUMMARY],
        "schema_version": SCHEMA_VERSION,
    }


def validate(p):
    """Returns a list of problems; empty means valid. Empty summary is allowed (the auditor flags it)."""
    errs = [k for k in ("event_id", "conversation_id", "ticket_id", "bot_outcome", "handover_flag",
                        "handover_summary", "schema_version") if k not in p]
    if p.get("bot_outcome") not in OUTCOMES:
        errs.append("bot_outcome")
    if not isinstance(p.get("handover_flag"), bool):
        errs.append("handover_flag")
    if not isinstance(p.get("handover_summary"), str):
        errs.append("handover_summary")
    return errs


def payload_hash(p):
    return hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()[:16]


class FakeTicketAPI:
    """Behaves like a helpdesk API: 503 while down, 404 for unknown tickets, idempotent on event_id,
    and it silently truncates long text at the vendor limit (which is why we read back)."""

    def __init__(self, con, down=None, vendor_limit=MAX_SUMMARY):
        self.con, self.down, self.limit = con, down, vendor_limit
        con.execute("CREATE TABLE IF NOT EXISTS processed_events(event_id TEXT PRIMARY KEY, processed_at_utc TEXT)")

    def put(self, p, now):
        if self.down and self.down[0] <= now < self.down[1]:
            return 503
        if validate(p):
            return 400
        if not self.con.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (p["ticket_id"],)).fetchone():
            return 404
        if self.con.execute("SELECT 1 FROM processed_events WHERE event_id=?", (p["event_id"],)).fetchone():
            return 200  # duplicate: acknowledged, no second update
        self.con.execute("UPDATE tickets SET bot_outcome=?, handover_flag=?, handover_summary=?, updated_at_utc=? "
                         "WHERE ticket_id=?", (p["bot_outcome"], int(p["handover_flag"]),
                                               p["handover_summary"][:self.limit], iso(now), p["ticket_id"]))
        self.con.execute("INSERT INTO processed_events VALUES (?,?)", (p["event_id"], iso(now)))
        return 200

    def get(self, ticket_id):
        r = self.con.execute("SELECT bot_outcome, handover_flag, handover_summary FROM tickets WHERE ticket_id=?",
                             (ticket_id,)).fetchone()
        return dict(zip(FIELDS, r)) if r else None


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS webhook_log(event_id TEXT, conversation_id TEXT, payload_hash TEXT, sent_at_utc TEXT,
        attempt INT, http_status INT, stored_ok INT, note TEXT);
    CREATE TABLE IF NOT EXISTS parked_events(event_id TEXT PRIMARY KEY, conversation_id TEXT, payload_json TEXT,
        parked_at_utc TEXT, reason TEXT);
    """)


def send_with_readback(con, api, p, ended_at):
    """Try, retry with backoff, read back after every 200. Returns 'ok', 'mismatch' or 'parked'.
    Never logs payload text, only its hash."""
    h = payload_hash(p)
    bad = validate(p)
    if bad:
        con.execute("INSERT INTO webhook_log VALUES (?,?,?,?,?,?,?,?)",
                    (p.get("event_id"), p.get("conversation_id"), h, iso(ended_at), 0, None, 0, "invalid:" + ",".join(bad)))
        con.execute("INSERT OR REPLACE INTO parked_events VALUES (?,?,?,?,?)",
                    (p.get("event_id"), p.get("conversation_id"), json.dumps(p), iso(ended_at), "invalid payload"))
        return "parked"
    status = None
    for attempt, wait in enumerate(BACKOFF_MIN, 1):
        now = ended_at + dt.timedelta(minutes=wait)
        status = api.put(p, now)
        ok, note = 0, ""
        if status == 200:
            back = api.get(p["ticket_id"])
            sent = {"bot_outcome": p["bot_outcome"], "handover_flag": int(p["handover_flag"]),
                    "handover_summary": p["handover_summary"]}
            diff = [f for f in FIELDS if back is None or back[f] != sent[f]]
            ok, note = int(not diff), ("mismatch:" + ",".join(diff)) if diff else ""
        con.execute("INSERT INTO webhook_log VALUES (?,?,?,?,?,?,?,?)",
                    (p["event_id"], p["conversation_id"], h, iso(now), attempt, status, ok, note))
        if status == 200:
            return "ok" if ok else "mismatch"
        if status in (400, 404):  # retrying will not help
            break
    con.execute("INSERT OR REPLACE INTO parked_events VALUES (?,?,?,?,?)",
                (p["event_id"], p["conversation_id"], json.dumps(p), iso(now), f"http {status}"))
    return "parked"


def rolling_alerts(con):
    """Hours where >2% of events ended parked or mismatched. One line per alert window."""
    rows = con.execute("""
        SELECT substr(first_sent, 1, 13) hr, COUNT(*) n, SUM(final_ok = 0) bad FROM (
          SELECT event_id, MIN(sent_at_utc) first_sent, MAX(stored_ok) final_ok FROM webhook_log GROUP BY event_id)
        GROUP BY hr HAVING 1.0 * bad / n > ? ORDER BY hr""", (ALERT_FAIL_RATE,)).fetchall()
    return [f"ALERT webhook {hr}:00Z  {bad}/{n} events not stored ({100 * bad / n:.0f}%)" for hr, n, bad in rows]


def main():
    con = sqlite3.connect(DB)
    ensure_tables(con)
    con.execute("DELETE FROM webhook_log")  # replay is a full rebuild of the simulated week
    con.execute("DELETE FROM parked_events")
    con.execute("DROP TABLE IF EXISTS processed_events")
    con.execute("UPDATE tickets SET bot_outcome=NULL, handover_flag=0, handover_summary=NULL")
    api = FakeTicketAPI(con, down=WEBHOOK_DOWN)
    rows = con.execute("""SELECT t.conversation_id, k.ticket_id, t.outcome, t.bot_handover_summary, t.ended_at_utc
                          FROM transcripts t JOIN tickets k USING (conversation_id)
                          WHERE t.handler = 'bot' AND t.outcome IS NOT NULL ORDER BY t.ended_at_utc""").fetchall()
    res = {"ok": 0, "mismatch": 0, "parked": 0}
    for cid, tid, outcome, summary, ended in rows:
        p = build_payload(cid, tid, outcome, summary)
        res[send_with_readback(con, api, p, dt.datetime.strptime(ended, "%Y-%m-%dT%H:%M:%SZ"))] += 1
    con.commit()
    n = len(rows)
    print(f"write-backs={n}  stored+read-back ok={res['ok']} ({100 * res['ok'] / n:.2f}%)  "
          f"mismatch={res['mismatch']}  parked={res['parked']}")
    for a in rolling_alerts(con):
        print(a)


if __name__ == "__main__":
    main()
