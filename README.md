# CX Analytics Projects

Five projects that mirror the day-to-day of an **Analyst, CX (Operations and Data)** at a pet-care e-commerce and clinic company: decision systems, outreach systems, CX data, one customer profile, and conversation quality.

> **All data in these projects is synthetic.** It has the same structure as a real e-commerce, pharmacy, vet-consult and clinic stack (orders, shipments, tickets, claims, bookings, transcripts), but contains no real customer, prescription or pet-health data. No real thresholds, fraud rules, vendor field names or internal numbers are used.

Status: **Project 1 built** ([p1_refund_decisions/](p1_refund_decisions/README.md)), **Project 3 built** ([p3_cx_pipeline/](p3_cx_pipeline/README.md)); the others are at specification stage. Each project file below is the full spec (problem, data, method, tests, measurement, runbook, requirement note).

---

## The five projects

| # | Project | JD area it proves | Spec |
| --- | --- | --- | --- |
| 1 | Refund and claim decision system | Decision systems, rules cut both ways, holdouts, weekly review | [projects/01-refund-claim-decision-system.md](projects/01-refund-claim-decision-system.md) |
| 2 | Order-delay early-warning outreach | Outreach systems, work lists, consent rules, spikes | [projects/02-order-delay-early-warning.md](projects/02-order-delay-early-warning.md) · built: [p2_order_delay_early_warning/](p2_order_delay_early_warning/README.md) |
| 3 | CX data pipeline, metrics and reconciliation | Own the CX data, systems you do not control | [projects/03-cx-data-pipeline-and-metrics.md](projects/03-cx-data-pipeline-and-metrics.md) · **built:** [p3_cx_pipeline/](p3_cx_pipeline/) |
| 4 | One customer profile and consent-safe outreach | One customer many identities, sensitive data | [projects/04-customer-profile-and-consent-safe-outreach.md](projects/04-customer-profile-and-consent-safe-outreach.md) |
| 5 | Conversation quality auditor and circuit breaker | Conversation systems, first on call, write-back checks | [projects/05-conversation-qa-and-circuit-breaker.md](projects/05-conversation-qa-and-circuit-breaker.md) |

If time is short, build **1 and 3** first (measurement plus data ownership), then 2.

## Coverage against the job description

| JD line | Project |
| --- | --- |
| One customer, many identities | 4 (and linked accounts in 1) |
| Systems you do not control | 3 |
| Every rule cuts both ways | 1 |
| Measuring what a system really did (holdouts) | 1, 2, 4 |
| Rules on every call and message (consent, DND, sensitive data) | 2, 4 |
| Spikes | 2, 3, 5 |
| Run decision, conversation, outreach systems and the customer profile | 1, 5, 2, 4 |
| Test every change on a small slice, read back what was stored | 1, 5 |
| Weekly report: decided, right, saved, cost when wrong | 1, 2, 4 |
| First on call: pause, tell owner, runbook, second on call | 5 |
| Build smaller systems (APIs, webhooks, alerts) | 3, 5 |
| Own the CX data (grain, keys, backfills, run logs, look-back) | 3 |
| Metric definitions, dashboards that disagree | 3 |
| Requirement notes signed off before Product | one in every project file |

---

## Shared synthetic dataset

All five projects read the same tables, so numbers reconcile across projects.

| Table | Grain | Key columns |
| --- | --- | --- |
| `accounts` | one row per account | account_id, phone, email, created_at, city, pincode |
| `orders` | one row per order | order_id, account_id, order_ts_utc, category, sku, value, lane, courier, promised_date, delivered_ts_utc, status, address_ok |
| `shipment_events` | one row per scan | order_id, event_ts_utc, status_code, location |
| `tickets` | one row per ticket | ticket_id, order_id (nullable), account_id, created_at_utc, updated_at_utc, channel, status, reason, first_response_ts_utc, resolved_ts_utc |
| `calls` | one row per call | call_id, phone, direction, start_ts_utc, duration_s, disposition |
| `claims` | one row per refund or replacement claim | claim_id, order_id, account_id, amount, reason, created_at_utc |
| `claim_audit` | ground truth for a sample | claim_id, is_genuine |
| `pharmacy_rx` | one row per prescription | rx_id, account_id, pet_id, medicine_code, refill_days, last_fill_date |
| `vet_consults` | one row per consult | consult_id, phone, pet_id, scheduled_ts_utc, status, specialty |
| `clinic_bookings` | one row per booking | booking_id, phone, clinic_id, slot_ts_utc, status |
| `consent` | one row per customer and channel | customer_id, channel, consent_status, dnd_flag, updated_at |
| `transcripts` | one row per conversation | conversation_id, handler (bot or agent), started_at_utc, messages_json, outcome |

Rules for the synthetic data:

- Generate with a fixed random seed so results are reproducible.
- Plant realistic patterns so analysis has something to find: a few lanes and one courier are late far more often, one category is often out of stock after the order, about 7% of addresses are wrong, a small group of people use several accounts and phone numbers, some tickets have no order id, some records are edited days after creation.
- Store timestamps in UTC; report in IST.

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

## Ground rules for every project

1. **Same format:** problem, approach, result with numbers, trade-off, what I would do next.
2. **Every number in rupees or orders,** with assumptions in one editable table.
3. **Holdout wherever a system acts on customers.**
4. **Consent, DND and sensitivity rules are built in,** not added at the end.
5. **Secrets stay out of code.** Use environment variables and a `.env.example`.
6. **Least data:** a table or list carries only the personal fields it needs.
7. **Back up before overwrite, read back after write.**
8. **Code in Git, reviewed, documented,** with one runnable check per project.
9. **Review AI-assisted code line by line** and say so in the pull request.

## Target repository layout (when built)

```
cx-analytics-projects/
  README.md
  projects/                 # the five specs
  common/                   # synthetic data generator, shared SQL, shared utils
  p1_refund_decisions/
  p2_order_delay_early_warning/
  p3_cx_pipeline/
  p4_customer_profile/
  p5_conversation_qa/
  docs/requirement_notes/   # one-page note per project
  .github/workflows/        # scheduled jobs and checks
```

## How to present these

- Open with the problem you saw on the floor, then the number the project moves.
- Show one trade-off in rupees per project, not only the win.
- Hand over the one-page requirement note for project 1; it is the clearest evidence of how you turn a vague request into a build.
- Be honest about limits: synthetic data, placeholder cost assumptions, and what would change with real data.
