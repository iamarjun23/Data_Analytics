# CX Analytics Projects

Five projects for an Analyst, Customer Experience (Operations and Data) role: decision systems, outreach systems, CX data, one customer profile, and conversation quality. **All data in these projects is synthetic.** It has the same structure as a real e-commerce, pharmacy, vet-consult and clinic stack (orders, shipments, tickets, claims, bookings, transcripts), but contains no real customer, prescription or pet-health data. No real thresholds, fraud rules, vendor field names or internal numbers are used.

## Projects

| # | Project | Problem | Headline result | Folder |
| --- | --- | --- | --- | --- |
| 1 | Refund and claim decision system | Agents decide every refund or replacement claim by hand, and nobody can say how many decisions are wrong. | Net weekly saving vs holdout of ₹11,547 at a ₹1,500 limit, with 55% of claims auto-decided (13-week window, 4,283 claims). | [p1_refund_decisions/](p1_refund_decisions/) |
| 2 | Order-delay early-warning outreach | Customers find out an order is late before we tell them, and they write in. | WISMO per 100 flagged orders: 14.4 treated vs 22.8 holdout (95% CI -14.2 to -2.7). | [p2_order_delay_early_warning/](p2_order_delay_early_warning/) |
| 3 | CX data pipeline, metrics and reconciliation | CX tables come from vendor platforms we do not control, so fields change and dashboards disagree. | 4 / 4 injected schema changes caught before load; 0 rows out of date after 14 days with a 3-day look-back (167 with none). | [p3_cx_pipeline/](p3_cx_pipeline/) |
| 4 | One customer profile and consent-safe outreach | One customer has many phones and accounts, and outreach must respect consent and DND. | 100% pair precision, 99.8% recall against planted truth; reorder lift +6.0 pts, net Rs 3,606 per run on simulated outcomes (95% interval -3.5 to +15.4). | [p4_customer_profile/](p4_customer_profile/) |
| 5 | Conversation quality auditor and circuit breaker | Bot answers can be wrong, lose context at handover, or be unsafe, and customers notice first. | Breaker opened 21 min after a bad prompt release, 7 min after a webhook outage and 1 min after an unsafe answer. | [p5_conversation_qa/](p5_conversation_qa/) |

All five projects are built, with code, tests and generated reports in each folder. If you have time for two, read 1 and 3.

## How every project works

1. Clean the data: normalise phones, timestamps (UTC, reported in IST) and status values.
2. Apply versioned rules or a score. A change means a new rule version, not an edit.
3. Keep a 10% holdout that skips the system, so its effect can be measured.
4. Log every decision with its score, reason and rule version, and read it back after writing.
5. Measure what was right and what was wrong, in orders or rupees, including the cost of wrong decisions.

## Run it

Python 3.10+ and the standard library only (SQLite). P3's README asks for 3.12 or later. Run from the repo root. `py main.py` runs all five in order.

```bash
py p1_refund_decisions/gen_data.py
py p2_order_delay_early_warning/simulate.py
cd p3_cx_pipeline && py test_pipeline.py
py p4_customer_profile/gen_identity_data.py
py p5_conversation_qa/gen_conversations.py
```

Each project README lists the rest of its commands.

## Shared metric definitions (used by every project)

Every metric states **population, date anchor, rule, denominator**.

| Metric | Population | Date anchor | Rule | Denominator |
| --- | --- | --- | --- | --- |
| Late delivery rate | Delivered orders | Promised date (IST) | delivered date later than promised date | Delivered orders with that promised date |
| WISMO contacts per 100 orders | All orders | Order date (IST) | tickets with reason `wismo` linked to the order within 14 days | Orders placed that day |
| Contacts per 100 orders | All orders | Order date (IST) | all tickets linked to the order within 14 days | Orders placed that day |
| First response time (FRT) | Tickets with a first response | Ticket created date (IST) | first_response_ts minus created_at, business hours excluded if stated | Tickets with a first response |
| Resolution rate (7 days) | Tickets created | Ticket created date (IST) | resolved within 7 days | Tickets created that day |
| Handover rate | Bot conversations | Conversation start date (IST) | conversation passed to an agent | Bot conversations started that day |
| Wrong approval rate | Auto-approved claims in audit sample | Claim created date (IST) | audit says not genuine | Auto-approved claims in sample |
| Wrong decline rate | Auto-declined claims in audit sample | Claim created date (IST) | audit says genuine | Auto-declined claims in sample |

## Honest limits

- All data is synthetic, so results show that the method works, not what real rates would be.
- P2 and P4 outcomes are simulated. The treatment effect is planted, and the P4 reorder-lift interval includes zero.
- P5's Claude model mode is written but has not been run. Rules mode produced the results. Its agreement with human labels is near-perfect because the labels and faults share one generator, so expect lower on real transcripts.
- P1's wrong declines sit at the 10% guardrail, and the decline rule needs a fix (see its README).
