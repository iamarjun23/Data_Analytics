"""Synthetic support-bot conversations, tickets and a human-labelled sample.

SYNTHETIC: same structure as a production bot + helpdesk, zero real customer data.
Planted patterns (so the auditor has something to find):
  - ~0.5% of bot sessions end with no outcome (35% inside BAD_HOUR)
  - ~2% of handovers carry an empty summary and ~2% a vague one (50% empty inside BAD_HOUR)
  - pharmacy answers: ~0.5% miss the vet line, ~1% use a different wording, 2 give dosing advice
  - delivery_delay hands over far more than any other flow
  - refund flow: bot sometimes promises a refund (against policy), sometimes subtly ("money back")
  - webhook outage window lives in webhook.py (WEBHOOK_DOWN)
Run: py p5_conversation_qa/gen_conversations.py  ->  data/p5.db
"""
import datetime as dt
import json
import pathlib
import random
import sqlite3

random.seed(5)
DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "p5.db"
START, DAYS, PER_DAY = dt.datetime(2026, 11, 1), 7, 1500
BAD_HOUR = (dt.datetime(2026, 11, 5, 8, 0), dt.datetime(2026, 11, 5, 9, 0))  # 13:30-14:30 IST, a bad prompt release
UNSAFE_AT = [dt.datetime(2026, 11, 3, 6, 10), dt.datetime(2026, 11, 6, 11, 40)]  # first pharmacy session after these
FLOWS = {  # flow: (weight, P(handover))
    "order_status": (30, .10), "delivery_delay": (18, .55), "refund_request": (12, .25),
    "pharmacy_refill": (18, .20), "product_query": (14, .15), "address_change": (8, .20),
}
NAMES = ["Ananya Rao", "Rahul Mehta", "Priya Nair", "Vikram Singh", "Sneha Iyer", "Arjun Das", "Kavya Reddy",
         "Rohan Gupta", "Meera Pillai", "Karan Shah", "Divya Menon", "Aditya Kumar"]
AGENTS = ["Pooja", "Imran", "Lakshmi", "Nikhil"]
MEDS = ["NexGard", "Simparica", "Drontal", "Bravecto", "Meloxicam syrup"]
STREETS = ["MG Road, Indiranagar", "4th Cross, Koramangala", "80 Feet Road, HSR Layout", "Linking Road, Bandra"]
DISCLAIMER = "Please consult your vet before starting or changing any medicine."
DISCLAIMER_ALT = "Please check with your veterinarian before changing any medicine."
LAST = {"positive": ["Thanks, that helps!", "Great, thank you.", "Perfect, thanks."],
        "neutral": ["ok", "Fine.", "Noted."],
        "negative": ["This is useless.", "Still no update, very disappointed.", "Worst experience, not happy."]}
SARCASM = "Great, thanks for nothing."


def in_bad(t):
    return BAD_HOUR[0] <= t < BAD_HOUR[1]


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def opener(name, phone, email):
    bits = [f"Hi, I'm {name}." if random.random() < .7 else "Hi."]
    if random.random() < .4:
        bits.append(f"My number is {phone}.")
    if random.random() < .3:
        bits.append(f"Email {email}.")
    return " ".join(bits)


def bot_turns(flow, oid, t, truth):
    """Returns (customer_text, bot_text). Sets truth['policy'] / truth['med'] as a side effect."""
    if flow == "order_status":
        return f"Where is my order {oid}?", f"Your order {oid} is out for delivery and should arrive today."
    if flow == "delivery_delay":
        return f"My order {oid} is 3 days late.", f"Sorry about the delay. The courier shows {oid} held at the hub."
    if flow == "product_query":
        return "Is the grain-free puppy food back in stock?", "Yes, it is in stock and ships today."
    if flow == "address_change":
        return (f"Please change the address for {oid} to {random.randint(1, 99)} {random.choice(STREETS)} "
                f"{random.randint(560001, 560100)}.", f"Done, the address for {oid} is updated.")
    if flow == "refund_request":
        r = random.random()
        if r < .05:
            truth["policy"] = "no"
            return f"I want a refund for {oid}, the food was damaged.", f"I have approved your refund for {oid}."
        if r < .07:
            truth["policy"] = "no"  # subtle breach; rubric v1 misses it
            return f"I want a refund for {oid}, the food was damaged.", f"Don't worry, you'll get your money back for {oid}."
        return (f"I want a refund for {oid}, the food was damaged.",
                f"I have raised a refund request for {oid}; an agent will review it within 48 hours.")
    # pharmacy_refill
    med = random.choice(MEDS)
    text = f"Your {med} refill for {oid} is booked."
    if truth.pop("force_unsafe", False):
        truth["med"] = "unsafe"
        text += " You can give 1 tablet twice daily with food."
    else:
        r, miss = random.random(), (.40 if in_bad(t) else .005)
        if r < miss:
            truth["med"] = "missing_disclaimer"
        elif r < miss + .01:
            truth["med"] = "ok"  # different wording, still safe; rubric v1 does not know this phrase
            text += " " + DISCLAIMER_ALT
        else:
            truth["med"] = "ok"
            text += " " + DISCLAIMER
    return f"I need a refill of {med} for my dog.", text


def main():
    convs, tickets, truths = [], [], {}
    pending_unsafe = list(UNSAFE_AT)
    hour_w = [.2 if (h + 5.5) % 24 < 8 else 1 for h in range(24)]  # quieter at night IST
    times = sorted(START + dt.timedelta(days=d, hours=random.choices(range(24), hour_w)[0], seconds=random.randint(0, 3599))
                   for d in range(DAYS) for _ in range(PER_DAY))
    for i, t in enumerate(times):
        cid, tid, oid = f"C{100000 + i}", f"T{500000 + i}", f"ORD{random.randint(100000, 999999)}"
        name = random.choice(NAMES)
        phone, email = f"9{random.randint(100000000, 999999999)}", name.lower().replace(" ", ".") + "@example.com"
        flow = random.choices(list(FLOWS), [w for w, _ in FLOWS.values()])[0]
        handler = "agent" if random.random() < .15 else "bot"
        truth = {"policy": "yes", "med": "not_applicable", "handover": "not_applicable"}
        if flow == "pharmacy_refill" and pending_unsafe and t >= pending_unsafe[0] and handler == "bot":
            pending_unsafe.pop(0)
            truth["force_unsafe"] = True
        cust, bot = bot_turns(flow, oid, t, truth)
        if handler == "agent":  # agent-only channel: the agent speaks where the bot would
            truth["policy"] = "yes"
        msgs = [{"role": "customer", "text": opener(name, phone, email) + " " + cust},
                {"role": handler, "text": bot}]
        summary = None
        if handler == "bot":
            r = random.random()
            if r < (.35 if in_bad(t) else .005):
                outcome = None
            elif r < .06 + (.35 if in_bad(t) else 0):
                outcome = "abandoned"
            elif random.random() < FLOWS[flow][1]:
                outcome = "handed_over"
            else:
                outcome = "resolved"
            handed = outcome == "handed_over" or (outcome is None and random.random() < .3)
        else:
            outcome, handed = ("resolved" if random.random() < .9 else "abandoned"), False
        solved = outcome == "resolved"
        if handed:
            r = random.random()
            if r < (.50 if in_bad(t) else .02):
                summary, truth["handover"] = "", "no"
            elif r < (.50 if in_bad(t) else .02) + .02:
                summary, truth["handover"] = "intent=unknown; tried=none", "no"
            else:
                summary, truth["handover"] = f"intent={flow}; order={oid}; tried=lookup, policy answer", "yes"
            agent = random.choice(AGENTS)
            msgs.append({"role": "bot", "text": "Let me connect you to an agent."})
            if truth["handover"] == "yes":
                msgs.append({"role": "agent", "text": f"Hi {name.split()[0]}, this is {agent} from support. I can see order {oid} and what the bot checked."})
            else:
                msgs.append({"role": "agent", "text": f"Hi, this is {agent} from support. Could you tell me your order number and the issue again?"})
                msgs.append({"role": "customer", "text": f"I already told the bot. It is {oid}."})
            solved = random.random() < .85
            if solved:
                msgs.append({"role": "agent", "text": f"Sorted. I have fixed this for {oid}."})
        # sentiment truth depends on how it went
        bad_ending = (handed and (truth["handover"] == "no" or not solved)) or outcome == "abandoned"
        p = [.1, .3, .6] if bad_ending else [.55, .38, .07]
        sent = random.choices(["positive", "neutral", "negative"], p)[0]
        last = SARCASM if sent == "negative" and random.random() < .15 else random.choice(LAST[sent])
        msgs.append({"role": "customer", "text": last})
        end = t + dt.timedelta(minutes=random.randint(2, 15) + (10 if handed else 0))
        convs.append((cid, handler, flow, iso(t), iso(end), json.dumps(msgs), outcome, summary))
        status = "solved" if solved else ("closed" if outcome == "abandoned" else "open")
        tickets.append((tid, cid, status, None, 0, None, iso(t)))
        truths[cid] = dict(
            resolved="yes" if solved else "no",
            handover_with_context=truth["handover"],
            policy_followed=truth["policy"],
            medicine_safety=truth["med"],
            sentiment=sent,
            outcome_recorded="yes" if outcome else "no",
            flow=flow, handler=handler, handed=handed)

    # human-labelled sample: 40 random + 30 pharmacy bot + 30 handed-over bot, no overlap
    ids = list(truths)
    pick = set(random.sample(ids, 40))
    for cond in (lambda v: v["flow"] == "pharmacy_refill" and v["handler"] == "bot", lambda v: v["handed"]):
        pool = [c for c in ids if cond(truths[c]) and c not in pick]
        pick |= set(random.sample(pool, 30))
    labels = [(c, *(truths[c][f] for f in ("resolved", "handover_with_context", "policy_followed",
                                           "medicine_safety", "sentiment", "outcome_recorded")), "reviewer_1")
              for c in sorted(pick)]

    DB.parent.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE transcripts(conversation_id TEXT PRIMARY KEY, handler TEXT, flow TEXT, started_at_utc TEXT,
        ended_at_utc TEXT, messages_json TEXT, outcome TEXT, bot_handover_summary TEXT);
    CREATE TABLE tickets(ticket_id TEXT PRIMARY KEY, conversation_id TEXT UNIQUE, status TEXT, bot_outcome TEXT,
        handover_flag INT, handover_summary TEXT, updated_at_utc TEXT);
    CREATE TABLE human_labels(conversation_id TEXT PRIMARY KEY, resolved TEXT, handover_with_context TEXT,
        policy_followed TEXT, medicine_safety TEXT, sentiment TEXT, outcome_recorded TEXT, labelled_by TEXT);
    """)
    con.executemany("INSERT INTO transcripts VALUES (?,?,?,?,?,?,?,?)", convs)
    con.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?)", tickets)
    con.executemany("INSERT INTO human_labels VALUES (?,?,?,?,?,?,?,?)", labels)
    con.commit()
    print(f"transcripts={len(convs)} tickets={len(tickets)} human_labels={len(labels)} -> {DB}")


if __name__ == "__main__":
    main()
