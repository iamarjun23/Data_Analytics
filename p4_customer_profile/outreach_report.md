# Overdue-refill outreach, run R20261115

SYNTHETIC data. **Outcomes are simulated** (planted uplift 8% on a 30% base) until real 14-day orders replace `simulate_outcomes()`.

## Funnel

| Step | Customers |
| --- | ---: |
| Overdue (refill_due + reorder_due) | 1,382 |
| excluded: no_consent | 260 |
| excluded: dnd | 116 |
| excluded: contact_cap | 28 |
| excluded: match_review | 1 |
| Eligible | 977 |
| Holdout (10%) | 102 |
| On the call list | 875 |

| Reason code | Arm | Customers |
| --- | --- | ---: |
| refill_due | contact | 124 |
| refill_due | holdout | 18 |
| reorder_due | contact | 751 |
| reorder_due | holdout | 84 |

## Lift (reorder within 14 days)

| Item | Contacted | Holdout | Difference |
| --- | ---: | ---: | ---: |
| Eligible customers | 875 | 102 | |
| Reordered within 14 days | 318 (36.3%) | 31 (30.4%) | +6.0 pts |
| Revenue | Rs 601,117 | Rs 57,633 | |
| Margin at 25% | Rs 150,279 | Rs 14,408 | |
| Opt-outs or complaints | 5 (0.6%) | 0 (0.0%) | |
| Agent minutes and cost | 3,500 min, Rs 21,000 | 0 | |

- Lift: **+6.0 pts**, 95% interval -3.5 to +15.4 pts (does not exclude zero: holdout too small to call it).
- Extra orders: 52 x Rs 1,890 x 25% margin - Rs 21,000 agent cost = **net Rs 3,606** for this run.
- Opt-out rate on contacted: 0.6%. Report it next to the lift, every run.

List columns sent to the team: customer_id (hashed), first_name, reason_code, preferred_channel, call_by, script_id.
