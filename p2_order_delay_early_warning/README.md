# P2: Order-delay early-warning outreach

_Synthetic data, same structure as production. No real customer data or internal numbers._

| | |
| --- | --- |
| **Status** | Built |
| **Spec** | [../projects/02-order-delay-early-warning.md](../projects/02-order-delay-early-warning.md) |
| **Requirement note** | [../docs/requirement_notes/p2_order_delay_early_warning.md](../docs/requirement_notes/p2_order_delay_early_warning.md) |
| **Runbook** | [RUNBOOK.md](RUNBOOK.md) |
| **Reports** | [report.md](report.md) (weekly impact) · [spike_result.md](spike_result.md) (load test) |
| **Check** | `test_job.py`, run in CI on every push |

## Problem

Customers find out an order is late, out of stock or stuck on a bad address before we tell them, and then they write in. Late deliveries are the most expensive CX problem in [P0](../p0_problem_ranking/README.md) (₹4.7L over 3 months), and most of the delay sits on three CourierX lanes. Today CX only reacts: nobody contacts the customer first, and nobody can say what proactive contact would save.

## What this does

Twice a day it flags every open order that is likely to go wrong. CX gets a work list that says what to send and to whom. Every row passes through consent, DND, quiet-hour and frequency-cap rules *inside* the job. 10% of flagged orders are held back, and a weekly report shows what the list actually changed.

```
orders + courier scans + consent
        │
        ▼
  job.py  ── signals ─► risk score ─► flag (≥ 0.40) ─► action + template
        │                                                  │
        │                               compliance filter (consent, DND, quiet hours, caps)
        │                                                  │
        │                                  10% holdout ◄───┤
        ▼                                                  ▼
  job_runs, risk_flags, exclusions                  outreach_list ─► send ─► outreach_log
                                                                               │
                                                              report.py ◄──────┘  treated vs holdout
```

## Approach

### 1. Risk signals

Each open order is scored from six signals. The weights and limits are at the top of [job.py](job.py).

| Signal | Fires when | Weight |
| --- | --- | --- |
| `lane_prior` | lane + courier late rate > 25% over the last 28 days | 0.25 |
| `scan_gap` | no courier scan for more than 36 hours | 0.30 |
| `packing_age` | not packed within the category SLA (12 h pharmacy, 24 h food, 36 h accessories) | 0.30 |
| `stock` | an item went out of stock after the order | 0.50 |
| `address` | the address is flagged as not deliverable | 0.50 |
| `promise` | promised date within 2 days and not yet out for delivery | 0.20 |

An order is flagged at a score of 0.40 or more. A bad lane alone (0.25) is **not** enough: it must combine with something about this order.

The job only sees what was visible at run time: scans by the time they were *ingested*, not the courier's event time. That avoids looking into the future in the backtest.

### 2. Actions

| Flag | Action | Template |
| --- | --- | --- |
| Lane or scan gap, order still moving | Delay notice with a new delivery date (lane's median transit + 1 day) | `T_DELAY` |
| Out of stock after order | Offer: wait, substitute or full refund | `T_OOS` |
| Address looks wrong | Confirm the address before dispatch | `T_ADDR` |
| Risk ≥ 0.70 and order value ≥ ₹2,200 | Agent callback | `T_CALL` |

Templates carry order updates only: no promotions, no product, medicine or pet-health detail.

### 3. Compliance filter (inside the job)

Applied before a row reaches the list. Every dropped row is written to `exclusions` with its reason.

1. **Consent:** message only on channels the customer consented to.
2. **DND:** blocks calls. Order updates by message are treated as transactional (to confirm with compliance).
3. **Quiet hours:** no contact outside 09:00–21:00 IST.
4. **Frequency caps:** one contact per order per 24 h; two proactive contacts per customer per 7 days.

### 4. Holdout

`hash(order_id) % 10 == 0` among **flagged** orders gets no proactive contact. Comparing flagged orders with unflagged ones would be wrong, because flagged orders are already different (they are the risky ones). Holdout customers still get normal service when they write in.

The pre-registered outcome is WISMO ("where is my order") tickets per 100 flagged orders.

### 5. Measurement

[report.md](report.md) compares treated with holdout on WISMO, all contacts, refunds and credits, cancellations and CSAT, with 95% confidence intervals. It also shows the cost side, false alarms and misses, the lane table, and a **compliance read-back**: six counts of stored rows that must all be 0.

## Results (simulated run, 29 Sep to 31 Oct 2026)

| Measure | Value |
| --- | --- |
| Late deliveries on the worst three lanes | 64.6% of late deliveries on 25.9% of delivered orders (all three on CourierX) |
| WISMO per 100 flagged orders: treated vs holdout | **14.4 vs 22.8**, a difference of −8.4 (95% CI −14.2 to −2.7) |
| All contacts per 100 flagged orders | 22.5 vs 33.8 (−11.3, 95% CI −17.9 to −4.6) |
| Tickets avoided | ~73 contacts a week (54 WISMO), ~₹4,000 a week at ₹55 a contact |
| Refunds and credits avoided | ₹1.83L over the window |
| Cost of running outreach | ₹1,269 (2,092 messages, 48 agent minutes on callbacks) |
| **Net** | **₹1.94L over 3.1 weeks** |
| False alarms and misses | 34% of flagged orders arrived on time; 8.6% of late orders were not flagged in time |
| Compliance read-back | 0 on all six checks |
| Run time | 0.10 s at 1x volume, 1.3 s at 10x. Never near the 30-minute cut-off |

### How to read the effect

The simulation plants an effect (`EFFECT` in [simulate.py](simulate.py)) that halves WISMO for customers who were actually reached. The report never reads `EFFECT`: it has to recover the effect from the holdout, as it would on real data. It finds a smaller drop (37%, not 50%) because the comparison is **intent to treat**: some flagged customers could not be messaged (no consent, cap, quiet hours). That gap is exactly why the holdout exists.

## Trade-off

At the 0.40 threshold, a third of flags are false alarms. A heads-up on an order that then arrives on time creates a few contacts of its own (planted at 3% → 6%), and the report shows that cost next to the win. To cut false alarms, raise `FLAG_AT` or drop `promise` as a standalone add-on, then rerun `simulate.py` and watch the miss rate rise. The right threshold is a business decision: it depends on how much a late surprise costs compared with an unnecessary message.

## Run it

Python 3.10+ and the standard library only. From the repo root:

```bash
python p2_order_delay_early_warning/test_job.py
```

```bash
python p2_order_delay_early_warning/simulate.py
```

```bash
python p2_order_delay_early_warning/spike_test.py
```

- `test_job.py` runs every check in a few seconds, on its own throwaway database.
- `simulate.py` builds `data/p2.db` from scratch, runs the job at 08:00 and 19:30 IST every day from 29 Sep to 31 Oct 2026, "sends" the list, realises tickets and refunds, and writes [report.md](report.md). It takes about 10 seconds. `report.py` can be rerun on its own against the stored tables.
- `spike_test.py` times one run at 1x, 2x, 3x and 10x order volume and writes [spike_result.md](spike_result.md). The run times in that file differ slightly on every machine.

## Files

| File | What it is |
| --- | --- |
| [gen.py](gen.py) | Synthetic accounts, consent, orders and courier scans with planted patterns. Reuses the lanes, categories and bad-address rate from [../common/gen_data.py](../common/gen_data.py) |
| [job.py](job.py) | The flag and list job: signals, score, action, compliance filter, holdout, exclusion log, run record. Every rule knob is at the top |
| [simulate.py](simulate.py) | Daily runs, a stand-in sender (3% failures, one retry), and the planted treatment effect that produces tickets, refunds and CSAT |
| [report.py](report.py) | Weekly impact report: treated vs holdout, cost side, false alarms and misses, lanes, compliance read-back, monitoring |
| [spike_test.py](spike_test.py) | The job at 1x / 2x / 3x / 10x volume |
| [test_job.py](test_job.py) | Each signal on fixture orders, actions, compliance filter, holdout, templates, and a read-back of stored rows |
| [RUNBOOK.md](RUNBOOK.md) | Alerts, how to pause, who to tell, rollout, incident template |

## Where this differs from the spec

- **SQLite, not DuckDB.** It keeps the project on the standard library. The lane query is the same logic.
- **Outcomes the treatment can move:** contacts, goodwill credits, out-of-stock substitutes and CSAT. Delivery dates and RTOs stay as they would have been; an address confirmation does not fix the address in this simulation.
- **Next-order rate within 30 days: skipped.** The synthetic world has no repeat-purchase model.
- **Opt-out and complaint rate: not modelled.** It stays a weekly runbook check.
- **Values the spec left blank:** weekly cap = 2 per customer; success threshold = 20% fewer WISMO tickets.

## What I would do next

- Feed the lane table to supply chain monthly. The cheapest fix for most of these tickets is a different courier on three lanes, not a message.
- Add address-quality checks at checkout, so the address problem does not happen in the first place.
- Model stock-outs with the category and inventory teams, so OOS orders are flagged before dispatch.
- Put the templates behind the [P5](../p5_conversation_qa/README.md) circuit breaker, so a bad template can be paused in minutes.
- Measure the next-order rate within 30 days once real data has the follow-up.
