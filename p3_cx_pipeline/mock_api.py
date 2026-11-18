"""Mock vendor API for 4 CX sources: ticketing, orders, telephony, bot.

SYNTHETIC: same shape as real vendor exports, no real customers, no real vendor field names.
Behaves like the real thing where it matters to the pipeline:
  - pages of records ordered by updated_at, filtered by updated_since / updated_until
  - a rate limit: every Nth call raises RateLimited (caller must retry with backoff)
  - each source uses its own date format (ISO UTC, dd/mm/yyyy IST, epoch ms)
  - records are edited later (tickets reopen, orders get delivered, bot outcomes arrive late)
  - some edits become visible days after their updated_at (corrected delivery times):
    an incremental load without a look-back misses these
  - drift switches: rename a field, add an unknown value, change a type
Every day's records come from a seed for that day, so the same day always has the same records.
"""
import datetime as dt
import random
import zlib

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
START = dt.date(2026, 10, 1)
PER_DAY = {"ticketing": 140, "orders": 260, "telephony": 70, "bot": 110}
H = dt.timedelta(hours=1)
D = dt.timedelta(days=1)


class RateLimited(Exception):
    pass


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def dmy_ist(t):
    return t.astimezone(IST).strftime("%d/%m/%Y %H:%M")


def epoch_ms(t):
    return int(t.timestamp() * 1000)


def wpick(rng, weights):
    return rng.choices(list(weights), list(weights.values()))[0]


QUEUES = {"q_orders_l1": 45, "q_pharmacy": 15, "q_refunds": 20, "q_vet": 8, "q_general": 12}


class MockAPI:
    def __init__(self, now, page_size=200, fail_every=7, drift=None, seed=3):
        self.now = now
        self.page_size = page_size
        self.fail_every = fail_every  # ponytail: deterministic "every Nth call" limit, not a real token bucket
        self.drift = drift or {}
        self.calls = 0
        self.versions = {s: [] for s in PER_DAY}  # (visible_at, updated_at, created_at, record)
        self._cache = {}
        d = START
        while d <= now.astimezone(IST).date():
            rng = random.Random(f"{seed}-{d}")
            day0 = dt.datetime.combine(d, dt.time(), IST)
            factor = 0.75 if d.weekday() >= 5 else 1.0
            for source, base in PER_DAY.items():
                for i in range(int(base * factor * rng.uniform(0.9, 1.1))):
                    getattr(self, "_" + source)(rng, d, i, day0 + rng.random() * D)
            d += D

    # ---- generators: each appends one record's versions -------------------------------
    def _add(self, source, created, updated, rec, visible=None):
        rec = dict(rec, updated_at=iso(updated))
        self.versions[source].append((visible or updated, updated, created, rec))
        self._cache.clear()

    def _ticketing(self, rng, d, i, t):
        c = t
        ref = None if rng.random() < 0.06 else f"O{(d - rng.randint(0, 6) * D):%y%m%d}{rng.randint(0, 230):04d}"
        r = dict(ticket_id=f"T{d:%y%m%d}{i:04d}", created_at=iso(t), status="new",
                 channel=wpick(rng, {"wa_inbound": 45, "email_in": 15, "voice_in": 15, "web_form": 10, "bot_handoff": 15}),
                 queue=wpick(rng, QUEUES), order_ref=ref, resolved_at=None,
                 requester_email=f"user{rng.randint(1, 99999)}@example.com" if rng.random() < 0.7 else None)
        self._add("ticketing", c, t, r)
        if rng.random() < 0.3:
            t += rng.uniform(1, 8) * H
            self._add("ticketing", c, t, r | {"status": "pending_customer"})
        if rng.random() < 0.85:
            t += rng.expovariate(1 / 18) * H + 0.2 * H
            r = r | {"status": "solved", "resolved_at": iso(t)}
            self._add("ticketing", c, t, r)
            if rng.random() < 0.08:  # reopened: vendor keeps the old resolved_at
                t += rng.uniform(1, 3) * D
                r = r | {"status": "reopened"}
                self._add("ticketing", c, t, r)
                if rng.random() < 0.6:
                    t += rng.uniform(2, 30) * H
                    self._add("ticketing", c, t, r | {"status": "solved", "resolved_at": iso(t)})
            elif rng.random() < 0.3:
                t += 3 * D
                self._add("ticketing", c, t, r | {"status": "closed"})

    def _orders(self, rng, d, i, t):
        created, promised = t, t + rng.choice([2, 3, 4, 5]) * D
        r = dict(order_id=f"O{d:%y%m%d}{i:04d}", created_at=dmy_ist(t), status="placed",
                 promised_at=dmy_ist(promised), delivered_at=None)
        self._add("orders", created, t, r)
        u = rng.random()
        if u < 0.05:
            t += rng.uniform(1, 6) * H
            return self._add("orders", created, t, r | {"status": "cancelled"})
        t += rng.uniform(8, 30) * H
        r = r | {"status": "shipped"}
        self._add("orders", created, t, r)
        if u < 0.10:
            t = promised + rng.uniform(1, 3) * D
            return self._add("orders", created, t, r | {"status": "rto"})
        late = rng.random() < 0.18
        t = promised + (rng.uniform(4, 72) if late else -rng.uniform(1, 40)) * H
        r = r | {"status": "delivered", "delivered_at": dmy_ist(t)}
        self._add("orders", created, t, r)
        if rng.random() < 0.06:  # courier corrects the delivered time; the edit shows up 1-2.5 days late
            fixed = t + rng.choice([-1, 1]) * rng.uniform(3, 20) * H
            upd = t + rng.uniform(2, 12) * H
            self._add("orders", created, upd, r | {"delivered_at": dmy_ist(fixed)}, visible=upd + rng.uniform(1, 2.5) * D)

    def _telephony(self, rng, d, i, t):
        disp = wpick(rng, {"ANS": 72, "NA": 10, "BUSY": 4, "VM": 4, "ABN": 10})
        dur = rng.randint(60, 600) if disp == "ANS" else rng.randint(0, 20 if disp == "ABN" else 40)
        r = dict(call_id=f"C{d:%y%m%d}{i:04d}", started_at=epoch_ms(t), disposition=disp,
                 queue=wpick(rng, QUEUES), duration_s=dur,
                 caller_phone=f"+9198{rng.randint(10**7, 10**8 - 1)}" if rng.random() < 0.95 else None)
        self._add("telephony", t, t + dur * dt.timedelta(seconds=1), r)
        if disp == "NA" and rng.random() < 0.3:  # disposition corrected next day
            self._add("telephony", t, t + D, r | {"disposition": "ANS", "duration_s": rng.randint(60, 300)})

    def _bot(self, rng, d, i, t):
        r = dict(conversation_id=f"B{d:%y%m%d}{i:04d}", started_at=iso(t),
                 channel=wpick(rng, {"wa_bot": 60, "web_widget": 25, "app_chat": 15}), outcome=None)
        self._add("bot", t, t, r)
        later = t + rng.uniform(2, 48) * H  # outcome lands up to 2 days after the chat
        self._add("bot", t, later, r | {"outcome": wpick(rng, {"bot_resolved": 55, "agent_handover": 30, "user_dropped": 15})})

    # ---- what the API exposes ------------------------------------------------------------
    def add_version(self, source, record, visible_at=None, created_at=None):
        """Test hook: push a new version of a record (an edit, a late edit, a new value)."""
        upd = dt.datetime.strptime(record["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        self.versions[source].append((visible_at or upd, upd, created_at or upd, dict(record)))
        self._cache.clear()

    def _current(self, source):
        """Latest visible version of each record, as the vendor would serve it right now."""
        k = (source, self.now)
        if k not in self._cache:
            key = {"ticketing": "ticket_id", "orders": "order_id", "telephony": "call_id", "bot": "conversation_id"}[source]
            latest = {}
            for vis, upd, created, rec in self.versions[source]:
                if vis <= self.now and upd <= self.now:
                    old = latest.get(rec[key])
                    if old is None or upd >= old[0]:
                        latest[rec[key]] = (upd, created, rec)
            self._cache[k] = sorted(latest.values(), key=lambda v: (v[0], v[2][key]))
        return self._cache[k]

    def fetch_page(self, source, updated_since=None, updated_until=None, cursor=0):
        self.calls += 1
        if self.fail_every and self.calls % self.fail_every == 0:
            raise RateLimited("429 Too Many Requests")
        rows = [rec for upd, _, rec in self._current(source)
                if (updated_since is None or upd > updated_since) and (updated_until is None or upd <= updated_until)]
        page = [self._drifted(source, rec) for rec in rows[cursor:cursor + self.page_size]]
        nxt = cursor + self.page_size
        return {"records": page, "next_cursor": nxt if nxt < len(rows) else None}

    def _drifted(self, source, rec):
        rec = dict(rec)
        for kind, (src, field, arg) in self.drift.items():
            if src != source:
                continue
            if kind == "rename" and field in rec:
                rec[arg] = rec.pop(field)
            elif kind == "new_value" and zlib.crc32(str(next(iter(rec.values()))).encode()) % 20 == 0:
                rec[field] = arg  # ~5% of records carry a value nobody mapped
            elif kind == "type" and rec.get(field) is not None:
                rec[field] = str(rec[field])
        return rec

    def daily_counts(self, source):
        """The vendor's own report: records created per IST day and queue (what we reconcile to)."""
        out = {}
        for _, created, rec in self._current(source):
            k = (created.astimezone(IST).date().isoformat(), rec.get("queue", "all"))
            out[k] = out.get(k, 0) + 1
        return out


def drift_from_env(text):
    """MOCK_DRIFT='rename:ticketing.channel>channel_name;new_value:ticketing.status=escalated_l2;type:telephony.duration_s'"""
    drift = {}
    for part in filter(None, (text or "").split(";")):
        kind, spec = part.split(":", 1)
        src, rest = spec.split(".", 1)
        field, _, arg = rest.replace(">", "=").partition("=")
        drift[kind] = (src, field, arg)
    return drift
