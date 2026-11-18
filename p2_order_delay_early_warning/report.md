# Order-delay early warning: weekly impact report

_Synthetic data, same structure as production. Treatment effect is measured from the 10% holdout; assumptions at the bottom._

Report window: orders promised **2026-10-02 to 2026-10-24** (IST), 5,747 orders, 2,245 flagged (39.1%). Runs: 66 (2026-09-29 to 2026-10-31, 08:00 and 19:30 IST).

## Results

| Measure | Value |
|---|---|
| Share of late orders carried by the worst three lanes | **64.6%** of late deliveries on 25.9% of delivered orders |
| WISMO per 100 flagged orders: treated vs holdout | **14.4 vs 22.8** (-8.4, 95% CI -14.2 to -2.7) |
| Tickets avoided per week and rupee value | **73 contacts/week** (54 WISMO), ₹4,000/week |
| False alarm rate and miss rate | **34.4%** of flagged orders arrived on time; **8.6%** of late orders were not flagged before the promised day ended |
| Run time at 1x, 2x, 3x volume | 1x: 0.11s average per run here; 2x / 3x: see [spike_result.md](spike_result.md) |

## Treated vs holdout (flagged orders only)

Pre-registered outcome: WISMO tickets per 100 flagged orders, from first flag to 7 days after the promised date. Holdout = `md5(order_id) % 10 == 0`. Intent to treat: treated includes flagged orders we could not message (no consent, cap), so the effect is not flattered.

| Item | Treated | Holdout | Difference (95% CI) |
|---|---|---|---|
| Flagged orders | 2,026 | 219 | |
| WISMO tickets per 100 | 14.4 | 22.8 | -8.4 (-14.2 to -2.7) |
| All contacts per 100 | 22.5 | 33.8 | -11.3 (-17.9 to -4.6) |
| Refunds and credits (₹ per 100 orders) | ₹19,823 | ₹28,849 | ₹-9,026 |
| Cancelled or RTO | 14.9% | 19.2% | -4.3 pts |
| CSAT (1-5, ticket surveys) | 3.49 (n=208) | 2.74 (n=39) | +0.75 |

### By week of first flag

| Week (Mon, IST) | Treated | Holdout | WISMO/100 treated | WISMO/100 holdout |
|---|---|---|---|---|
| 2026-09-28 | 501 | 47 | 14.0 | 29.8 |
| 2026-10-05 | 635 | 76 | 14.2 | 21.1 |
| 2026-10-12 | 614 | 69 | 15.6 | 18.8 |
| 2026-10-19 | 276 | 27 | 13.0 | 25.9 |

_Holdout weeks are small (about 10% of flags); read the weekly rows as a trend, the total as the answer._

## Cost side

| Item | Value |
|---|---|
| Messages sent (incl. retries) / callbacks attempted | 2,092 / 8 |
| Message delivery failures after retry | 11 orders |
| Agent minutes on callbacks | 48 |
| Contacts avoided over the window | 229 (₹12,572) |
| Refunds and credits avoided | ₹1.83L |
| Cost of running outreach (messages + callback minutes) | ₹1,269 |
| **Net** | **₹1.94L** over 3.1 weeks |

Refunds avoided are mostly out-of-stock customers who took a substitute instead of cancelling; treat that line as revenue kept, not cost cut.

## Where the delay sits (lane analysis)

| Lane | Courier | Delivered | Late | Late % |
|---|---|---|---|---|
| BLR-NE | CourierX | 246 | 140 | 56.9% |
| BLR-KOL | CourierX | 437 | 233 | 53.3% |
| BLR-DEL | CourierX | 689 | 321 | 46.6% |
| BLR-MUM | CourierB | 589 | 74 | 12.6% |
| BLR-PUN | CourierB | 361 | 39 | 10.8% |
| BLR-CHN | CourierA | 592 | 60 | 10.1% |
| BLR-HYD | CourierA | 652 | 61 | 9.4% |
| BLR-BLR | Express | 1,731 | 146 | 8.4% |

Owner: supply chain (courier choice, EDD buffer on these lanes). This project gives the evidence; it does not switch couriers.

## What the list asked CX to do

| Action | Listed | Held out |
|---|---|---|
| delay_notice | 2,267 | 240 |
| address_confirm | 598 | 57 |
| oos_offer | 398 | 36 |
| callback | 13 | 1 |

| Exclusion reason | Orders | Rows logged |
|---|---|---|
| holdout | 332 | 334 |
| cap | 39 | 112 |
| no_consent | 14 | 14 |
| dnd | 3 | 3 |
| quiet_hours | 1 | 1 |

`quiet_hours` and `cap` rows are retried at the next run, so one order can log several. `dnd` / `no_consent` on a callback downgrade it to a message order update.

## Compliance read-back (stored rows, each should be 0)

| Check | Count |
|---|---|
| Sends outside 09:00-21:00 IST | 0 |
| Holdout orders that received any outreach | 0 |
| Sends on a channel without consent | 0 |
| Calls to DND customers | 0 |
| Same order contacted twice within 24h | 0 |
| Customer contacted more than 2x in 7 days | 0 |

## Monitoring

| Signal | This window |
|---|---|
| Runs that missed the 30-min cut-off | 0 of 66 |
| Morning flag rate > 1.5x its trailing 4-week average | none |
| UNMAPPED courier codes | HUB_HOLD_X: 369 (alerted in 66 runs) |
| Message delivery failures after one retry | 11 |
| Opt-out / complaint rate | not modelled in synthetic data; weekly check in the runbook |

## Data coverage

- Tickets with an order id: **96.2%** (WISMO tickets: 96.0%). Tickets without one are left out of every per-order number above.
- Courier scans that reached us more than 1h after the event: **4.4%**. The job reads scans by ingested time and re-reads all open orders every run.
- Timestamps stored in UTC; dates, weeks, quiet hours and run times in IST.

## Assumptions

- `contact_cost` = 55
- `msg_cost` = 0.4
- `callback_min` = 6
- `agent_cost_min` = 9
- Rule knobs live at the top of `job.py` (flag at 0.4, scan gap 36h, lane late rate 25%, weekly cap 2).
- New EDD = lane's 28-day median actual transit (order date to delivery, IST days) + 1 day buffer.
