# Conversation quality report (rubric v2, rules mode)
SYNTHETIC data, 1-7 Nov 2026. Dates in IST. Rates: resolved excludes 'unclear'; handover-with-context is over handovers.

## Daily quality table
| date (IST) | conversations | resolved | handover w/ context | policy followed | medicine issues | negative | outcome recorded |
|---|---|---|---|---|---|---|---|
| 2026-11-01 | 1366 | 90.5% | 94.3% | 99.2% | 1 | 12.7% | 99.5% |
| 2026-11-02 | 1486 | 90.4% | 96.3% | 98.9% | 1 | 13.1% | 99.7% |
| 2026-11-03 | 1513 | 92.4% | 92.2% | 99.5% | 3 | 10.4% | 96.6% |
| 2026-11-04 | 1500 | 90.0% | 95.9% | 99.2% | 1 | 11.8% | 99.6% |
| 2026-11-05 | 1508 | 91.3% | 92.2% | 99.5% | 5 | 11.8% | 98.3% |
| 2026-11-06 | 1522 | 91.1% | 95.7% | 99.5% | 3 | 11.0% | 99.3% |
| 2026-11-07 | 1479 | 91.0% | 96.8% | 99.7% | 1 | 11.6% | 99.5% |
| 2026-11-08 | 126 | 93.5% | 90.5% | 99.2% | 0 | 11.1% | 98.4% |

## By flow and handler (week)
| flow | handler | conversations | resolved | handover w/ context | policy followed | medicine issues | negative | outcome recorded |
|---|---|---|---|---|---|---|---|---|
| address_change | agent | 149 | 91.9% | - | 100.0% | 0 | 10.7% | 100.0% |
| address_change | bot | 673 | 92.4% | 90.8% | 100.0% | 0 | 9.4% | 97.9% |
| delivery_delay | agent | 290 | 87.9% | - | 100.0% | 0 | 17.9% | 100.0% |
| delivery_delay | bot | 1629 | 85.9% | 96.2% | 100.0% | 0 | 15.4% | 99.1% |
| order_status | agent | 464 | 92.2% | - | 100.0% | 0 | 10.1% | 100.0% |
| order_status | bot | 2650 | 93.7% | 89.8% | 100.0% | 0 | 9.6% | 98.3% |
| pharmacy_refill | agent | 321 | 90.3% | - | 100.0% | 1 | 13.1% | 100.0% |
| pharmacy_refill | bot | 1628 | 91.2% | 96.3% | 100.0% | 14 | 11.2% | 99.0% |
| product_query | agent | 239 | 89.1% | - | 100.0% | 0 | 13.4% | 100.0% |
| product_query | bot | 1245 | 92.2% | 93.5% | 100.0% | 0 | 12.6% | 99.0% |
| refund_request | agent | 173 | 91.3% | - | 100.0% | 0 | 10.4% | 100.0% |
| refund_request | bot | 1039 | 90.4% | 96.2% | 93.3% | 0 | 11.5% | 98.7% |

## Flows with most handovers (bot sessions)
| flow | bot sessions | handed over | handover rate | sample ids for the owner |
|---|---|---|---|---|
| delivery_delay | 1629 | 840 | 51.6% | C100010, C100021, C100029 |
| refund_request | 1039 | 234 | 22.5% | C100041, C100117, C100132 |
| pharmacy_refill | 1628 | 295 | 18.1% | C100051, C100068, C100123 |
| address_change | 673 | 116 | 17.2% | C100115, C100170, C100319 |
| product_query | 1245 | 183 | 14.7% | C100096, C100273, C100279 |
| order_status | 2650 | 258 | 9.7% | C100105, C100124, C100126 |

## Safety list (15) - send to the pharmacy owner the same day
| conversation | date | time IST | flow | status | rule note |
|---|---|---|---|---|---|
| C103351 | 2026-11-03 | 11:54:18 | pharmacy_refill | unsafe | dosing advice |
| C108383 | 2026-11-06 | 17:30:19 | pharmacy_refill | unsafe | dosing advice |
| C100104 | 2026-11-01 | 09:00:37 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C101377 | 2026-11-02 | 00:05:47 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C103540 | 2026-11-03 | 14:09:06 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C104175 | 2026-11-03 | 21:54:22 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C104800 | 2026-11-04 | 11:08:23 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C106121 | 2026-11-05 | 09:24:03 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C106499 | 2026-11-05 | 13:40:09 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C106501 | 2026-11-05 | 13:40:36 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C106504 | 2026-11-05 | 13:41:18 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C106517 | 2026-11-05 | 13:50:25 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C108310 | 2026-11-06 | 16:41:38 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C108539 | 2026-11-06 | 19:36:16 | pharmacy_refill | missing_disclaimer | no vet-consult line |
| C108952 | 2026-11-07 | 01:38:01 | pharmacy_refill | missing_disclaimer | no vet-consult line |

## Auditor vs human labels
Human sample: 100 conversations (40 random, 30 pharmacy, 30 handovers).

| rubric / mode | field | n | agreement | Cohen's kappa |
|---|---|---|---|---|
| v1 rules | resolved | 100 | 99.0% | 0.96 |
| v1 rules | handover_with_context | 100 | 98.0% | 0.96 |
| v1 rules | policy_followed | 100 | 100.0% | 1.00 |
| v1 rules | medicine_safety | 100 | 99.0% | 0.98 |
| v1 rules | sentiment | 100 | 99.0% | 0.98 |
| v1 rules | outcome_recorded | 100 | 100.0% | 1.00 |
| v2 rules | resolved | 100 | 99.0% | 0.96 |
| v2 rules | handover_with_context | 100 | 100.0% | 1.00 |
| v2 rules | policy_followed | 100 | 100.0% | 1.00 |
| v2 rules | medicine_safety | 100 | 100.0% | 1.00 |
| v2 rules | sentiment | 100 | 100.0% | 1.00 |
| v2 rules | outcome_recorded | 100 | 100.0% | 1.00 |

### v1 disagreements (what drove rubric v2)
| field | auditor said | human said | count | example ids |
|---|---|---|---|---|
| handover_with_context | yes | no | 2 | C100765, C104669 |
| resolved | unclear | no | 1 | C100108 |
| medicine_safety | missing_disclaimer | ok | 1 | C100207 |
| sentiment | positive | negative | 1 | C103914 |

## Write-back (webhook -> ticket fields)
- Events: 8792. Stored and read back matching: 8748 (99.5%). Parked after retries: 44.
- Recovered by retry: 2. Read-back mismatches: 0.

## Circuit breaker (replay of the week)
| state | changed at (UTC) | reason |
|---|---|---|
| open | 2026-11-02T12:54:53Z | handover_without_context_share=4/19 (21.1% > 15.0%) for 5 min |
| half_open | 2026-11-02T13:24:53Z | owner review done; test slice running |
| closed | 2026-11-02T13:54:53Z | test slice healthy |
| open | 2026-11-03T06:34:53Z | medicine_unsafe: unsafe answer confirmed (opens on first case) |
| half_open | 2026-11-03T07:04:53Z | owner review done; test slice running |
| closed | 2026-11-03T07:34:53Z | test slice healthy |
| open | 2026-11-03T09:06:53Z | webhook_failure_rate=13/86 (15.1% > 2.0%) for 5 min |
| half_open | 2026-11-03T09:36:53Z | owner review done; test slice running |
| closed | 2026-11-03T10:06:53Z | test slice healthy |
| open | 2026-11-05T08:20:53Z | no_outcome_share=6/71 (8.5% > 5.0%) for 5 min |
| half_open | 2026-11-05T08:50:53Z | owner review done; test slice running |
| open | 2026-11-05T09:20:53Z | test slice still bad: no_outcome_share=7/36 (19.4% > 5.0%) |
| half_open | 2026-11-05T09:50:53Z | owner review done; test slice running |
| closed | 2026-11-05T10:20:53Z | test slice healthy |
| open | 2026-11-06T12:09:53Z | medicine_unsafe: unsafe answer confirmed (opens on first case) |
| half_open | 2026-11-06T12:39:53Z | owner review done; test slice running |
| closed | 2026-11-06T13:09:53Z | test slice healthy |

## Weekly summary
Top problems by count: negative endings (1233); sessions with no outcome in the ticket (116); handovers without context (102).
Recommendation: see README results section (owner: Product + bot vendor).
