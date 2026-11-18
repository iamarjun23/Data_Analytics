"""Circuit breaker for the support bot: replays the week minute by minute and logs every state change.

closed --(a limit breached for SUSTAIN_MIN, or any unsafe medicine answer)--> open
open --(owner review, timed here at OPEN_REVIEW_MIN)--> half_open
half_open --(test slice healthy for HALF_OPEN_MIN)--> closed      half_open --(still bad)--> open
After a close, windows restart from the close time so pre-fix events do not re-trip it.

Run: py p5_conversation_qa/breaker.py [--rubric 2]   (after auditor.py and webhook.py)
"""
import argparse
import bisect
import datetime as dt
import pathlib
import sqlite3

from gen_conversations import BAD_HOUR, UNSAFE_AT
from webhook import WEBHOOK_DOWN

DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "p5.db"
SYSTEM = "support_bot"  # ponytail: one breaker for the whole bot; split per flow if one flow's pause should not stop others
LIMITS = {  # metric: (window minutes, max share, min sessions in window). None = 4-week baseline + 10 points
    "no_outcome_share": (60, .05, 30),
    "handover_without_context_share": (60, .15, 10),
    "medicine_missing_disclaimer_share": (1440, .02, 30),
    "webhook_failure_rate": (60, .02, 20),
    "negative_sentiment_share": (1440, None, 100),
}
MIN_COUNT = 4          # never open on a handful of events, whatever the share
SUSTAIN_MIN = 5        # "limit exceeded for N minutes"
OPEN_REVIEW_MIN = 30   # stands in for the owner's review in the replay
HALF_OPEN_MIN = 30     # length of the test slice
# Not watched yet: wrong-answer reports from agents (needs an agent report feed).


class Series:
    """Sorted events with prefix sums so any window's (num, den) is two bisects."""

    def __init__(self, rows):  # rows: (time, num 0/1, den 0/1)
        rows = sorted(rows)
        self.t = [r[0] for r in rows]
        self.num, self.den = [0], [0]
        for _, n, d in rows:
            self.num.append(self.num[-1] + n)
            self.den.append(self.den[-1] + d)

    def window(self, a, b):  # events with a < time <= b
        i, j = bisect.bisect_right(self.t, a), bisect.bisect_right(self.t, b)
        return self.num[j] - self.num[i], self.den[j] - self.den[i]


def check(series, limits, a_floor, t, slice_start=None):
    """Breached metrics at time t: {name: reason}."""
    out = {}
    for name, (win, lim, min_den) in limits.items():
        a = slice_start if slice_start else max(t - dt.timedelta(minutes=win), a_floor)
        n, d = series[name].window(a, t)
        if d >= (min_den if not slice_start else 1) and n >= MIN_COUNT and n / d > lim:
            out[name] = f"{name}={n}/{d} ({100 * n / d:.1f}% > {100 * lim:.1f}%)"
    return out


def replay(series, unsafe, limits, start, end):
    """Returns [(time, state, reason)]. unsafe: sorted times of unsafe medicine answers."""
    state, since, changes = "closed", {}, []
    floor = opened = ho_start = start
    us = Series([(u, 1, 1) for u in unsafe])
    t = start
    while t <= end:
        prev = t - dt.timedelta(minutes=1)
        if state == "closed":
            if us.window(max(prev, floor), t)[0]:
                state, opened = "open", t
                changes.append((t, state, "medicine_unsafe: unsafe answer confirmed (opens on first case)"))
            else:
                hit = check(series, limits, floor, t)
                since = {k: since.get(k, t) for k in hit}
                long_ = [hit[k] for k in hit if t - since[k] >= dt.timedelta(minutes=SUSTAIN_MIN)]
                if long_:
                    state, opened, since = "open", t, {}
                    changes.append((t, state, "; ".join(long_) + f" for {SUSTAIN_MIN} min"))
        elif state == "open" and t - opened >= dt.timedelta(minutes=OPEN_REVIEW_MIN):
            state, ho_start = "half_open", t
            changes.append((t, state, "owner review done; test slice running"))
        elif state == "half_open":
            if us.window(ho_start, t)[0]:
                state, opened = "open", t
                changes.append((t, state, "medicine_unsafe in test slice"))
            elif t - ho_start >= dt.timedelta(minutes=HALF_OPEN_MIN):
                hit = check(series, limits, floor, t, slice_start=ho_start)
                if hit:
                    state, opened = "open", t
                    changes.append((t, state, "test slice still bad: " + "; ".join(hit.values())))
                else:
                    state, floor = "closed", t
                    changes.append((t, state, "test slice healthy"))
        t += dt.timedelta(minutes=1)
    return changes


def load(con, rubric):
    p = lambda s: dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    rows = [(p(e), *r) for e, *r in con.execute("""
        SELECT t.ended_at_utc, a.outcome_recorded, a.handover_with_context, a.medicine_safety, a.sentiment
        FROM transcripts t JOIN audit_scores a USING (conversation_id)
        WHERE t.handler = 'bot' AND a.rubric_version = ? AND a.mode = 'rules'""", (rubric,))]
    hooks = [(p(s), 1 - ok, 1) for s, ok in con.execute(
        "SELECT MIN(sent_at_utc), MAX(stored_ok) FROM webhook_log GROUP BY event_id")]
    med = lambda m: m not in ("not_applicable",)
    series = {
        "no_outcome_share": Series([(t, o == "no", 1) for t, o, h, m, s in rows]),
        "handover_without_context_share": Series([(t, h == "no", h != "not_applicable") for t, o, h, m, s in rows]),
        "medicine_missing_disclaimer_share": Series([(t, m == "missing_disclaimer", med(m)) for t, o, h, m, s in rows]),
        "webhook_failure_rate": Series(hooks),
        "negative_sentiment_share": Series([(t, s == "negative", 1) for t, o, h, m, s in rows]),
    }
    unsafe = sorted(t for t, o, h, m, s in rows if m == "unsafe")
    start = min(r[0] for r in rows)
    first48 = [r for r in rows if r[0] < start + dt.timedelta(hours=48)]
    baseline = sum(r[4] == "negative" for r in first48) / len(first48)  # ponytail: 48h stands in for the 4-week baseline until 4 weeks exist
    return series, unsafe, baseline, start, max(r[0] for r in rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rubric", type=int, default=2)
    a = ap.parse_args()
    con = sqlite3.connect(DB)
    series, unsafe, base, start, end = load(con, a.rubric)
    limits = dict(LIMITS, negative_sentiment_share=(1440, base + .10, 100))
    changes = replay(series, unsafe, limits, start, end)
    con.executescript("""DROP TABLE IF EXISTS breaker_state;
        CREATE TABLE breaker_state(system TEXT, state TEXT, changed_at_utc TEXT, reason TEXT);""")
    con.executemany("INSERT INTO breaker_state VALUES (?,?,?,?)",
                    [(SYSTEM, s, t.strftime("%Y-%m-%dT%H:%M:%SZ"), r) for t, s, r in changes])
    con.commit()
    print(f"negative-sentiment baseline {100 * base:.1f}% -> limit {100 * (base + .10):.1f}%")
    for t, s, r in changes:
        print(f"{(t + dt.timedelta(hours=5, minutes=30)):%a %d %b %H:%M} IST  {s:<9} {r}")
    for label, f in [("bad prompt release", BAD_HOUR[0]), ("webhook outage", WEBHOOK_DOWN[0])] + \
                     [(f"unsafe answer #{i + 1}", u) for i, u in enumerate(unsafe)]:
        opened = next((t for t, s, _ in changes if s == "open" and t >= f), None)
        print(f"fault -> open, {label}: {(opened - f).total_seconds() / 60:.0f} min" if opened else f"{label}: never opened")


if __name__ == "__main__":
    main()
