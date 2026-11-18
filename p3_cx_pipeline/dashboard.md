# CX daily dashboard

Built from `mart_daily` (definitions in [metrics.md](metrics.md)). Last run: `20261118T0030-161732` finished 2026-11-18 05:29:51 UTC. Dates are IST. `*` = provisional (outcomes still arriving).

| Day (IST) | tickets_created | resolved_tickets | late_orders | late_order_rate | handover_rate | call_answer_rate |
|---|---|---|---|---|---|---|
| 2026-11-06 | 141 | 91 | 30 | 16.5% | 30.8% | 81.5% |
| 2026-11-07 | 112 | 104 | 46 | 19.5% | 37.0% | 71.7% |
| 2026-11-08 | 105 | 102 | 43 | 18.0% | 36.2% | 74.5% |
| 2026-11-09 | 142 | 105 | 48 | 21.4% | 29.5% | 77.0% |
| 2026-11-10 | 133 | 112 | 23 | 13.2% | 31.6% | 66.2% |
| 2026-11-11 | 142 | 118 | 22 | 12.4% | 34.6% | 72.3% |
| 2026-11-12 | 148 | 119 | 30 | 16.4% | 34.3% | 71.8% |
| 2026-11-13 | 141 | 98 | 33 | 15.0% | 22.8% | 75.3% |
| 2026-11-14 | 104 | 118 | 46 | 20.3% | 27.0% | 71.9% |
| 2026-11-15 | 105 | 93 | 33* | 13.5%* | 23.9% | 77.1% |
| 2026-11-16 | 145 | 111 | 9* | 5.0%* | 24.6% | 78.6% |
| 2026-11-17 | 141 | 105 | 1* | 0.5%* | 10.0%* | 68.0% |
| 2026-11-18 | 31 | 36 | 0* | 0.0%* | 3.0%* | 85.0% |
| 2026-11-19 |  |  | 0* | 0.0%* |  |  |

## Pipeline health (latest run)

| Table | Status | In | New | Changed | Rejected | Watermark |
|---|---|---|---|---|---|---|
| ticketing | success | 710 | 140 | 105 | 0 | 2026-11-18 00:24:29 |
| orders | success | 1704 | 239 | 401 | 0 | 2026-11-18 00:25:49 |
| telephony | success | 252 | 76 | 3 | 0 | 2026-11-18 00:29:48 |
| bot | success | 532 | 120 | 79 | 0 | 2026-11-18 00:29:24 |

## Data-quality checks not passing

- none

## Reconciliation to source (last 14 days)

- all days match
