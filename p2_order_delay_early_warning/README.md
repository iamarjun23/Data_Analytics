# Project 2: Order-delay early-warning outreach

_Synthetic data, same structure as production. No real customer data or internal numbers._
Spec: [../projects/02-order-delay-early-warning.md](../projects/02-order-delay-early-warning.md)

**Problem.** Customers find out an order is late, out of stock, or stuck on a bad address before we tell them,
and they write in. **What this does.** It flags every open order twice a day, gives CX a work list that says
what to send and to whom, filters every row for consent, DND, quiet hours and frequency caps, holds 10% back,
and reports what the list actually changed.

## Run it

Python 3.10+, standard library only (sqlite3 stands in for DuckDB; the SQL ports directly).

```bash
py p2_order_delay_early_warning/test_job.py
```

```bash
py p2_order_delay_early_warning/simulate.py
```

```bash
py p2_order_delay_early_warning/spike_test.py
```

`simulate.py` builds `data/p2.db` from scratch, runs the job at 08:00 and 19:30 IST from 29 Sep to 31 Oct 2026,
"sends" the list, realises tickets and refunds, then writes [report.md](report.md). It takes about 10 s.
`report.py` can be rerun on its own against the stored tables.

| File | What it is |
| --- | --- |
| `gen.py` | Synthetic accounts, consent, orders, courier scans with planted patterns (reuses lanes and categories from `common/gen_data.py`) |
| `job.py` | Risk flag + daily list job: signals, score, action, compliance filter, holdout, exclusion log, run record. Every rule knob sits at the top. |
| `simulate.py` | Daily runs, a stand-in sender (3% failures, one retry), and the planted treatment effect that produces tickets, refunds and CSAT |
| `report.py` | Weekly impact report: treated vs holdout, cost side, false alarms and misses, lanes, compliance read-back, monitoring |
| `spike_test.py` | Job at 1x / 2x / 3x / 10x volume -> [spike_result.md](spike_result.md) |
| `test_job.py` | Each signal on fixture orders, actions, compliance filter, holdout, templates, read-back of stored rows |

## Results (from report.md and spike_result.md)

| Measure | Value |
| --- | --- |
| Share of late orders carried by the worst three lanes | 64.6% of late deliveries on 25.9% of delivered orders (all three on CourierX) |
| WISMO per 100 flagged orders: treated vs holdout | 14.4 vs 22.8, a difference of -8.4 (95% CI -14.2 to -2.7) |
| Tickets avoided per week and rupee value | ~73 contacts/week (54 WISMO), ~₹4,000/week at ₹55 a contact |
| False alarm rate and miss rate | 34% of flagged orders arrived on time; 8.6% of late orders were not flagged in time |
| Run time at 1x, 2x, 3x volume | 0.10 s, 0.22 s, 0.34 s (10x: 1.3 s). Never near the 30-min cut-off. |

The planted effect (`EFFECT` in `simulate.py`) halves WISMO for customers we actually reached. The report never
reads it, and it recovers a smaller drop because it is intent to treat: some flagged customers could not be
messaged. That gap is the reason for the holdout.

**Trade-off.** At a 0.40 threshold, a third of flags are false alarms, and a heads-up on an order that arrives on
time creates some contacts of its own (planted at 3% → 6%). The report shows both sides. To cut false alarms,
raise `FLAG_AT` or drop `promise` as a standalone add-on, then rerun and watch the miss rate rise.

## Where this differs from the spec

- **sqlite, not DuckDB.** DuckDB isn't installed and stdlib covers it. The lane query is the same logic.
- **Outcomes the treatment can move:** contacts, goodwill credits, out-of-stock substitutes, and CSAT. Delivery
  dates and RTOs stay as they would have been; an address confirmation does not fix the address in this simulation.
- **Next-order rate within 30 days: skipped.** The synthetic world has no repeat-purchase model. Add it when
  real data has 30 days of follow-up.
- **Opt-out and complaint rate:** not modelled. It stays a weekly runbook check.
- **Placeholders the spec left blank:** weekly cap = 2 per customer, success threshold = 20% (below).

## Runbook

The list runs at 08:00 IST and is due by 08:30 (cut-off). An evening top-up runs at 19:30. Each run writes a
row to `job_runs`.

| Signal | Check | Action |
| --- | --- | --- |
| List not ready by cut-off | `job_runs.ready_by_cutoff = 0`; the job logs an error | Alert the owner. CX works yesterday's list (`outreach_list` for the previous `list_date`) and skips orders that have since been delivered. Rerun once the cause is fixed; the rerun adds only rows not already decided. |
| Flag rate jumps | Morning flagged/open > 1.5x the trailing 4-week average (report, Monitoring) | Check the data before you act: a courier feed outage looks like a scan gap on every order. Pause sends if a feed is down. |
| UNMAPPED courier codes | `job_runs.unmapped > 0`; warning logged each run | Map the new code in `job.KNOWN` with the courier team. Until then it still counts as a scan, so it does not create false gaps. |
| Messages failing to send | Failed after one retry, per run | Alert the messaging owner. Failures are kept in `outreach_log`, and the frequency cap counts list rows, not deliveries, so nobody is double-messaged. |
| Opt-out or complaint rate | Weekly | Pause the template (remove it from `TEMPLATE_OF`), review the content with CX and compliance, relaunch on one lane. |
| Compliance read-back not 0 | Report, Compliance section | Stop sends. Any non-zero row means a rule was bypassed; find the run in `job_runs`, fix, rerun tests. |

Rollout (spec 13): unit tests → test orders and numbers with read-back → shadow week (lists produced, nothing
sent, compare flags with outcomes) → one-lane pilot (BLR-DEL, `T_DELAY` only) with the holdout live → widen
lane by lane after each weekly review.

## Requirement note

**Problem.** Customers contact us about late and at-risk orders before we contact them. 65% of late deliveries
sit on three CourierX lanes that carry a quarter of delivered orders.

**Proposed change.** Flag at-risk open orders twice daily. Give CX a work list with a recommended action and a
template. Message early with a corrected EDD (lane median transit + 1 day).

**Rules.** Flag at risk score ≥ 0.40 from lane late rate (> 25%, 28 days), scan gap (> 36 h), packing age
(over the category's SLA), stock and address signals, and a near promise date. Respect consent, DND (calls only,
since message order updates are transactional), quiet hours 09:00-21:00 IST, one contact per order per 24 h,
and two per customer per week.

**Data.** orders, shipment_events, tickets, consent. Outputs: risk_flags, outreach_list, outreach_log,
exclusions, job_runs.

**Test plan.** Unit tests, then test orders and numbers with read-back, one shadow week, and a one-lane pilot
with a 10% holdout.

**Success measure.** WISMO tickets per 100 flagged orders at least 20% lower in treated than in holdout, with no
rise in cancellations and no fall in CSAT. (Synthetic run: 37% lower.)

**Risks.** A wrong EDD in a message. Mitigation: use the lane's measured transit plus a buffer, and review false
alarms weekly. One in three flags is a false alarm at the current threshold.

**Owner and sign-off.** The manager signs off before the note goes to Product and supply chain.
