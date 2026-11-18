"""Metric definitions in code. One entry per metric in metrics.md; the dashboard and the
disagreement tool both compute from here, so they cannot drift apart.

Each metric: source table, kind (count | rate), anchor (which timestamp decides the day),
pop (population = denominator), rule (numerator, within pop), provisional_days.
`alt` = how a typical other dashboard defines it (used by disagree.py to test the "denominator" cause).
Rows are staging rows: timestamps are 'YYYY-MM-DD HH:MM:SS' UTC.
"""
import datetime as dt

OFFSET = {"IST": dt.timedelta(hours=5, minutes=30), "UTC": dt.timedelta(0)}


def day(ts, tz="IST"):
    return (dt.datetime.fromisoformat(ts) + OFFSET[tz]).date().isoformat()


def created(r):
    return r["created_at"]


def started(r):
    return r["started_at"]


def late(r):
    """Shared definition (root README): delivered DATE later than promised DATE, both IST."""
    return day(r["delivered_at"]) > day(r["promised_at"])


METRICS = {
    "tickets_created": dict(
        source="ticketing", kind="count", anchor=created,
        pop=lambda r: True, rule=lambda r: True,
        alt=dict(pop=lambda r: r["channel_group"] != "bot", note="leaves out bot handoffs (counted in the bot report)")),
    "resolved_tickets": dict(
        source="ticketing", kind="count", anchor=lambda r: r["resolved_at"],
        pop=lambda r: r["resolved_at"] is not None, rule=lambda r: r["status_group"] == "resolved",
        alt=dict(rule=lambda r: True, note="counts any ticket with a resolved time, including ones reopened since")),
    "late_orders": dict(
        source="orders", kind="count", anchor=lambda r: r["promised_at"], provisional_days=4,
        pop=lambda r: r["status_group"] == "delivered", rule=late,
        alt=dict(pop=lambda r: r["status_group"] in ("delivered", "returned"),
                 rule=lambda r: r["status_group"] == "returned" or late(r),
                 note="counts returned-to-origin orders as late")),
    "late_order_rate": dict(
        source="orders", kind="rate", anchor=lambda r: r["promised_at"], provisional_days=4,
        pop=lambda r: r["status_group"] == "delivered", rule=late,
        alt=dict(pop=lambda r: r["status_group"] in ("delivered", "returned"),
                 rule=lambda r: r["delivered_at"] is not None and late(r),
                 note="adds returned-to-origin orders to the denominator")),
    "handover_rate": dict(
        source="bot", kind="rate", anchor=started, provisional_days=2,
        pop=lambda r: True, rule=lambda r: r["outcome_group"] == "handed_over",
        alt=dict(pop=lambda r: r["outcome_group"] is not None, note="only conversations that already have an outcome")),
    "call_answer_rate": dict(
        source="telephony", kind="rate", anchor=started,
        pop=lambda r: True, rule=lambda r: r["outcome"] == "answered",
        alt=dict(pop=lambda r: r["outcome"] == "answered" or r["duration_s"] >= 5, note="drops calls under 5 s (IVR hang-ups)")),
}


def compute(rows, name, tz="IST", definition="ours", filters=None):
    """{day: (numerator, denominator)} for one metric over a list of staging rows."""
    m = METRICS[name]
    m = m | m["alt"] if definition == "alt" else m
    out = {}
    for r in rows:
        if filters and any(str(r.get(k)) != v for k, v in filters.items()):
            continue
        if not m["pop"](r) or m["anchor"](r) is None:
            continue
        num, den = out.get(d := day(m["anchor"](r), tz), (0, 0))
        out[d] = (num + bool(m["rule"](r)), den + 1)
    return out


def value(name, num, den):
    return num if METRICS[name]["kind"] == "count" else (round(num / den, 4) if den else None)


def total(name, by_day, start, end):
    """One number for a date range (inclusive IST/UTC dates as strings)."""
    num = sum(n for d, (n, _) in by_day.items() if start <= d <= end)
    den = sum(x for d, (_, x) in by_day.items() if start <= d <= end)
    return value(name, num, den)
