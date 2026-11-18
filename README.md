# CX Analytics Projects

[![ci](https://github.com/iamarjun23/Data_Analytics/actions/workflows/ci.yml/badge.svg)](https://github.com/iamarjun23/Data_Analytics/actions/workflows/ci.yml)
[![cx-pipeline](https://github.com/iamarjun23/Data_Analytics/actions/workflows/cx_pipeline.yml/badge.svg)](https://github.com/iamarjun23/Data_Analytics/actions/workflows/cx_pipeline.yml)

One problem-sizing analysis and five working systems that mirror the day-to-day of an **Analyst, CX (Operations and Data)** at a pet-care e-commerce, pharmacy and clinic company. They cover decision systems, outreach systems, owning the CX data, one customer profile, and conversation quality.

Every project is **built, tested and runs end to end** with Python and its standard library only: no installs, no API keys, no database server.

> **All data is synthetic.** It has the same structure as a real e-commerce, pharmacy, vet-consult and clinic stack (orders, courier scans, tickets, claims, calls, bookings, transcripts) but contains no real customer, prescription or pet-health data. No real thresholds, fraud rules, vendor field names or internal numbers are used. Patterns are *planted* so the analysis has something to find, and each project's README lists them.

---

## Contents

- [Start here](#start-here)
- [The projects at a glance](#the-projects-at-a-glance)
- [Headline results](#headline-results)
- [How the projects connect](#how-the-projects-connect)
- [Quick start](#quick-start)
- [Repository layout](#repository-layout)
- [Data](#data)
- [Shared metric definitions](#shared-metric-definitions)
- [Ground rules for every project](#ground-rules-for-every-project)
- [Coverage against the job description](#coverage-against-the-job-description)
- [Continuous integration](#continuous-integration)
- [How to present these](#how-to-present-these)
- [Limits](#limits)

---

## Start here

1. **[P0: where CX breaks](p0_problem_ranking/README.md).** Before building anything, rank the CX problems by what they cost in rupees. Late deliveries (42%), out of stock after order (29%) and wrong addresses (23%) make up ₹11.2L over 3 months. The five projects follow from that ranking.
2. **[P1](p1_refund_decisions/README.md) and [P3](p3_cx_pipeline/README.md)** are the strongest evidence of measurement and data ownership. If time is short, read these two.
3. **[docs/requirement_notes/](docs/requirement_notes/)** has a one-page note per project, written the way it would go to a manager for sign-off before Product sees it.

## The projects at a glance

Each project folder has a README (problem, approach, results, trade-off, how to run it, what next), a runbook, tests, and generated reports. The full original spec for each project is in [projects/](projects/).

| # | Project | What it proves | README | Requirement note | Runbook | Tests | Spec |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | Where CX breaks, ranked in rupees | Sizing problems before building; coverage on every join | [README](p0_problem_ranking/README.md) | n/a (analysis) | n/a | CI run | n/a |
| 1 | Refund and claim decision system | Decision systems; rules cut both ways; holdouts; versioned rules | [README](p1_refund_decisions/README.md) | [note](docs/requirement_notes/p1_refund_decisions.md) | [runbook](p1_refund_decisions/RUNBOOK.md) | 18 + end-to-end | [spec](projects/01-refund-claim-decision-system.md) |
| 2 | Order-delay early-warning outreach | Outreach systems; work lists; consent rules; spikes | [README](p2_order_delay_early_warning/README.md) | [note](docs/requirement_notes/p2_order_delay_early_warning.md) | [runbook](p2_order_delay_early_warning/RUNBOOK.md) | script checks | [spec](projects/02-order-delay-early-warning.md) |
| 3 | CX data pipeline, metrics and reconciliation | Owning data from systems you don't control | [README](p3_cx_pipeline/README.md) | [note](docs/requirement_notes/p3_cx_pipeline.md) | [runbook](p3_cx_pipeline/RUNBOOK.md) | 10 | [spec](projects/03-cx-data-pipeline-and-metrics.md) |
| 4 | One customer profile and consent-safe outreach | One customer, many identities; sensitive data | [README](p4_customer_profile/README.md) | [note](docs/requirement_notes/p4_customer_profile.md) | [runbook](p4_customer_profile/RUNBOOK.md) | 6 | [spec](projects/04-customer-profile-and-consent-safe-outreach.md) |
| 5 | Conversation QA auditor and circuit breaker | Conversation systems; first on call; write-back checks | [README](p5_conversation_qa/README.md) | [note](docs/requirement_notes/p5_conversation_qa.md) | [runbook](p5_conversation_qa/RUNBOOK.md) | 21 | [spec](projects/05-conversation-qa-and-circuit-breaker.md) |

## Headline results

All numbers come from the synthetic runs and are reproduced by the commands in each README.

| # | Headline | Trade-off shown |
| --- | --- | --- |
| 0 | ₹11.2L of CX cost over 3 months, ranked. The 3 worst lanes (all one courier) carry **62% of late deliveries on 26% of orders**. The ranking holds when contact cost and margin each move ±30%. | Sizes depend on placeholder costs; the out-of-stock cost ranges from ₹2.3L to ₹4.3L with margin. |
| 1 | 55% of claims auto-decided at a ₹1,500 limit; **net ₹11,547 a week** vs holdout; ~33 agent-hours a week saved; wrong approvals 2.5%. | Wrong declines sit at the 10% guardrail: two honest heavy buyers. The next rule fixes that. |
| 2 | WISMO tickets per 100 flagged orders **14.4 vs 22.8** (treated vs holdout, 95% CI of the gap −14.2 to −2.7); **net ₹1.94L over 3 weeks**; 0 compliance breaches. | 34% of flags are false alarms; 8.6% of late orders are missed. |
| 3 | **4 of 4** schema changes caught before load; **324 of 324** unknown values routed to UNMAPPED; **0** stale rows with a 3-day look-back (167 without); 100% of dashboard gaps explained by cause. | Failing on a rename delays that source by a day. |
| 4 | **100% match precision, 99.8% recall**; 1,068 customers found with more than one phone; a shared clinic desk phone blocked from merging strangers; 0 DND customers listed. | One run's holdout is too small: lift +6.0 pts but the interval includes zero. |
| 5 | Breaker opened **21 min** after a bad prompt release, **7 min** into a webhook outage, **1 min** after an unsafe dosing answer; auditor agreement with humans 99–100% (kappa ≥ 0.96). | Tightening limits to cut false pauses (7 → 1 a week) slows detection of low-volume faults. |

## How the projects connect

```
                 P0  where CX breaks (₹ ranking)
                  │
     ┌────────────┼──────────────────────┐
     ▼            ▼                      ▼
 late deliveries  out of stock      wrong address
     │            │                      │
     ├──► P2 early warning: tell the customer first (delay notice, OOS offer, address check)
     └──► P1 claim decisions: settle the claims that still come in, fast and fairly
                  │
 P4 one customer profile ──► who is this person, may we contact them?  (feeds P1 and P2 linking/consent)
 P3 data pipeline ─────────► the tickets, orders, calls and bot data every report above relies on
 P5 conversation QA ───────► the bot that answers "where is my order?" before an agent does
```

## Quick start

Requirements: **Python 3.10 or later** (CI uses 3.12). Standard library only, so there is nothing to install. On Windows, `py` works wherever `python` is written below.

```bash
git clone https://github.com/iamarjun23/Data_Analytics.git
```

```bash
cd Data_Analytics
```

Run every project's checks (the same commands CI runs):

```bash
python common/gen_data.py && python p0_problem_ranking/ranked_problems.py > /dev/null
```

```bash
python p1_refund_decisions/gen_data.py && (cd p1_refund_decisions && python -m unittest && python engine.py && python report.py > /dev/null)
```

```bash
python p2_order_delay_early_warning/test_job.py
```

```bash
(cd p3_cx_pipeline && python test_pipeline.py)
```

```bash
python p4_customer_profile/test_profile.py
```

```bash
python -m unittest p5_conversation_qa/test_p5.py
```

Each project's README has the full run, which also regenerates its reports. Databases are written to `data/` (gitignored). Settings that a real deployment would keep secret are listed in [.env.example](.env.example); the synthetic runs need none of them.

## Repository layout

```
Data_Analytics/
  README.md                       # this file
  .env.example                    # settings a real deployment needs (no secrets in code)
  common/
    gen_data.py                   # shared synthetic dataset (orders, tickets, refunds, returns) -> data/cx.db
  p0_problem_ranking/             # where CX breaks, ranked in rupees
  p1_refund_decisions/            # claim decision engine, versioned rules, sweep, weekly report
    rules/v1.json                 #   the versioned rule config
    out/                          #   generated report.md and sweep.svg
  p2_order_delay_early_warning/   # risk flag, work list, compliance filter, holdout, spike test
  p3_cx_pipeline/                 # incremental loader, contracts, mappings, checks, reconciliation
    pipeline/                     #   run, checks, reconcile, metrics, disagree
    maps/                         #   mapping tables as CSV (changed by pull request)
  p4_customer_profile/            # crosswalk, customer_360, coverage report, refill outreach
  p5_conversation_qa/             # auditor, rubric v1/v2, webhook write-back, circuit breaker
  projects/                       # the original spec for each project
  docs/requirement_notes/         # one-page requirement note per project, ready for sign-off
  .github/workflows/
    ci.yml                        # every project's checks on each push and pull request
    cx_pipeline.yml               # P3 scheduled daily at 06:00 IST
```

## Data

### What is shared, and what is not

All projects use the **same synthetic world**: the same lanes and couriers (BLR-DEL, BLR-KOL and BLR-NE on CourierX are the slow ones), the same product categories, the same ~7% bad-address rate, UTC storage with IST reporting, and fixed random seeds. So the *findings* agree across projects: P0, P1 and P2 all find the delay on the same three CourierX lanes.

They do **not** all read one database. Each project generates the tables it needs at the grain it needs, so it can run on its own in seconds:

| Database | Built by | Used by | Why it is separate |
| --- | --- | --- | --- |
| `data/cx.db` | [common/gen_data.py](common/gen_data.py) | P0 | Daily-grain orders, tickets, refunds, returns over 3 months |
| `data/p1.db` | [p1_refund_decisions/gen_data.py](p1_refund_decisions/gen_data.py) | P1 | Needs claims, an audit sample, multi-account abusers and households |
| `data/p2.db` | [p2_order_delay_early_warning/gen.py](p2_order_delay_early_warning/gen.py) (imports lanes, categories and the bad-address rate from `common/`) | P2 | Needs courier scans at timestamp grain and consent |
| `data/p3.db` | [p3_cx_pipeline/mock_api.py](p3_cx_pipeline/mock_api.py) via the pipeline | P3 | Data arrives through a mock vendor API with edits and drift |
| `data/p4.db` | [p4_customer_profile/gen_identity_data.py](p4_customer_profile/gen_identity_data.py) | P4 | Needs nine messy identity sources and a planted answer key |
| `data/p5.db` | [p5_conversation_qa/gen_conversations.py](p5_conversation_qa/gen_conversations.py) | P5 | Needs full transcripts and human labels |

Because the volumes and windows differ, **rupee totals are not meant to add up across projects.** Each project's numbers stand on their own. Moving all generators onto `common/` is listed under [Limits](#limits).

### Tables (logical model)

The table names and grain below are the shared vocabulary. Each project uses the subset it needs.

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

### Planted patterns

- A few lanes and one courier are late far more often.
- One category often goes out of stock after the order.
- About 7% of addresses are wrong (half end as return to origin).
- One SKU (BED-L) is returned about 6× more than its peers, mostly for size.
- A small group of people use several accounts and phone numbers; a clinic desk phone links strangers.
- Some tickets have no order id; some records are edited days after creation.
- A bad bot prompt release, a webhook outage and two unsafe dosing answers (P5).

## Shared metric definitions

Every metric states **population, date anchor, rule and denominator**. P3 codes these once in [p3_cx_pipeline/metrics.md](p3_cx_pipeline/metrics.md), and every dashboard uses that code.

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
2. **Every number in rupees or orders,** with assumptions in one editable place.
3. **A holdout wherever a system acts on customers** (P1, P2, P4), randomised by hash so it is reproducible.
4. **Consent, DND and sensitivity rules are built in,** not added at the end: filters run inside the job, and every exclusion is logged.
5. **Secrets stay out of code.** Use environment variables and [.env.example](.env.example).
6. **Least data:** a table or list carries only the personal fields it needs; phones and emails are hashed outside the source.
7. **Back up before overwrite, read back after write.**
8. **Code in Git, reviewed, documented,** with runnable checks per project in CI.
9. **AI-assisted code is reviewed line by line,** and the pull request says so.

## Coverage against the job description

| JD line | Project |
| --- | --- |
| Find and size the problems first | 0 |
| One customer, many identities | 4 (and linked accounts in 1) |
| Systems you do not control | 3 |
| Every rule cuts both ways | 1 |
| Measuring what a system really did (holdouts) | 1, 2, 4 |
| Rules on every call and message (consent, DND, sensitive data) | 2, 4 |
| Spikes | 2, 3, 5 |
| Run decision, conversation, outreach systems and the customer profile | 1, 5, 2, 4 |
| Test every change on a small slice, read back what was stored | 1, 2, 4, 5 |
| Weekly report: decided, right, saved, cost when wrong | 1, 2, 4 |
| First on call: pause, tell owner, runbook, second on call | every project's RUNBOOK; 5 has the circuit breaker |
| Build smaller systems (APIs, webhooks, alerts) | 3, 5 |
| Own the CX data (grain, keys, backfills, run logs, look-back) | 3 |
| Metric definitions, dashboards that disagree | 3 |
| Requirement notes signed off before Product | [docs/requirement_notes/](docs/requirement_notes/), one per project |

## Continuous integration

| Workflow | When | What it does |
| --- | --- | --- |
| [ci.yml](.github/workflows/ci.yml) | every push and pull request | Runs each project's checks as its own job (P0 to P5), so one failure does not hide the others |
| [cx_pipeline.yml](.github/workflows/cx_pipeline.yml) | daily at 06:00 IST, or by hand | Runs P3 for real: tests, incremental load with a 3-day look-back, checks, reconciliation, and uploads the dashboard as a build artifact |

## How to present these

- Open with **P0**: the problem you saw on the floor, and what it costs.
- For each project, give the number it moves, then **one trade-off in rupees**, not only the win.
- Hand over the [P1 requirement note](docs/requirement_notes/p1_refund_decisions.md). It is the clearest evidence of turning a vague request into a build.
- Show a failure being handled: inject schema drift into P3 (`MOCK_DRIFT=rename:ticketing.channel>channel_name`) and walk through the alert and the runbook.
- Be honest about limits: synthetic data, placeholder costs, and what would change with real data.

## Limits

- **Synthetic data.** Effects are planted, so near-perfect results (P4 precision, P5 agreement) will be lower on real data. Each README says where.
- **Placeholder costs.** Agent cost, margin and thresholds are editable placeholders, not company numbers.
- **Separate databases.** Projects share conventions and lanes but not one database (see [Data](#data)). The next step is to have every generator read `common/`, so totals reconcile across projects.
- **SQLite stands in for a warehouse** (DuckDB or Postgres in the specs). The SQL ports directly.
- **P5 model mode has not been run.** It sends redacted text to an external API and needs approval first.
