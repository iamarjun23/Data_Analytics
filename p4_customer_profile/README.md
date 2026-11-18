# P4: One customer profile and consent-safe outreach

_Synthetic data. No real customer, prescription or pet-health data._

| | |
| --- | --- |
| **Status** | Built |
| **Spec** | [../projects/04-customer-profile-and-consent-safe-outreach.md](../projects/04-customer-profile-and-consent-safe-outreach.md) |
| **Requirement note** | [../docs/requirement_notes/p4_customer_profile.md](../docs/requirement_notes/p4_customer_profile.md) |
| **Runbook** | [RUNBOOK.md](RUNBOOK.md) |
| **Reports** | [coverage_report.md](coverage_report.md) (every build) · [outreach_report.md](outreach_report.md) (every list run) |
| **Check** | `test_profile.py` (6 tests), run in CI on every push |

## Problem

One pet parent shows up under several accounts and phone numbers across seven systems: orders, tickets, calls, pharmacy, vet consults, clinic bookings and consent. The effects:

- **History is split.** An agent sees one account, not the customer.
- **Outreach is unsafe.** Someone who opted out on one number can still be called on another.
- **Measurement is undercounted.** Repeat claims, repeat contacts and reorders are spread across ids.

In the synthetic data, 1,068 of 5,001 customers have more than one phone, and 515 have more than one account.

## What this does

It builds a **crosswalk** (every source record → one `customer_id`), a **`customer_360` view** with consent, DND and sensitivity flags, and a **coverage report** with every build. It then uses the profile for its first real job: a **measured overdue-refill call list** with a compliance filter and a 10% holdout.

```
accounts, orders, tickets, calls, pharmacy_rx, vet_consults, clinic_bookings, consent
        │
        ▼  normalise (phones → last 10 digits, emails lower-case, addresses hashed)
  build_profile.py ── tiers A/B/C ─► clusters ─► guards (shared phone, size) ─► customer_xwalk
        │                     tier D ─► match_candidates (review only)               │
        │                                                                          ▼
        ├─► coverage_report.md  (build HELD if coverage drops > 3 pts)        customer_360
        └─► clinic_duplicates                                                      │
                                                                                   ▼
                                        refill_outreach.py ─► compliance filter ─► 10% holdout ─► list + read-back
```

## Approach

### 1. Normalise

Phone numbers are cut to their last 10 digits (`+91 98450-12345`, `09845012345` and `9845012345` all become one number). Emails are lower-cased and addresses are hashed with the pincode.

### 2. Match in tiers (deterministic, no ML)

| Tier | Rule | Confidence | Used for |
| --- | --- | --- | --- |
| A | same normalised phone, or a direct link (order or ticket → account) | high | merge |
| B | same normalised email | high | merge |
| C | same address hash + same pet name | medium | merge, counted separately in coverage |
| D | same pincode + pet name + first name | low | **review queue only, never merged** |

Pincode + pet name alone is never a match: on 5,000 people it produced thousands of false pairs.

### 3. Guard against wrong merges

- **Shared-phone guard.** A number that is nobody's account phone, but would join two accounts, is never used to link. That catches a clinic desk phone even when only two strangers share it. The size limit alone would miss that.
- **Cluster size limit.** More than 5 accounts or 5 phones in one cluster sends it to `cluster_review` instead of being accepted.
- **Match quality per customer** is the weakest tier among its accounts. Only customers at tier A or B are ever contacted.

### 4. Coverage report, every build

For each source: records, matched share, tier C share, and *why* the rest did not match. If any source's coverage falls by more than 3 points against the last build, the build is **held**: rolled back, with the previous profile kept.

### 5. Clinic duplicate bookings

Active bookings for the same customer, pet and specialty in the same week are flagged for an agent to confirm. Nothing is cancelled automatically.

### 6. First consumer: overdue-refill list

1. **Find overdue customers.** `refill_due` means a prescription is past its refill days plus a 3-day grace. `reorder_due` means it has been more than 1.5× the customer's usual gap since their last order.
2. **Filter.** Exclude DND, no consent on the channel, anyone contacted in the last 7 days, and match quality below B. Every exclusion is logged with its reason.
3. **Hold out 10%** by hash of the customer id.
4. **Write the list, read it back**, and abort if the stored list differs from what was built or contains a blocked customer.
5. **Record who ran it, when and why.** `--purpose` is required.
6. **Report the lift.** Reorders within 14 days, contacted vs holdout, with a 95% interval, agent cost and opt-outs.

The list carries a hashed customer id, first name, reason code, channel, call-by time and script id. **No medicine names.**

## Results (synthetic, data as of 15 Nov 2026)

| Measure | Value |
| --- | --- |
| Customers with more than one phone | 1,068 of 5,001 (515 with more than one account) |
| Coverage per source | orders, tickets, pharmacy 100%; clinic bookings 98.3%; calls 92.9%; vet consults 81.6% ([coverage_report.md](coverage_report.md)) |
| Match quality vs planted truth | **100% pair precision, 99.8% recall** (1 pair missed: a second account linked by nothing) |
| Clusters flagged for review | 0 over the size limit; 1 shared phone (the clinic desk) blocked from linking, its 12 walk-in bookings left unmatched |
| Tier D candidates | 182 waiting. Only 1 is really the same person: 181 would be wrong merges, which is why tier D only ever goes to review |
| Duplicate clinic slots | 9 / 6 / 9 groups (weeks 46–48); 0 groups that were really two people |
| Outreach funnel | 1,382 overdue → 405 excluded (260 no consent, 116 DND, 28 contact cap, 1 match review) → 875 called, 102 held out |
| Reorder lift vs holdout | **+6.0 pts** (36.3% vs 30.4%), 95% CI −3.5 to +15.4 |
| Net value | ₹3,606 per run (52 extra orders × ₹1,890 × 25% margin − ₹21,000 agent cost), on **simulated** outcomes |
| Opt-outs on contacted customers | 0.6% |

### Where coverage is low, and why

Vet consults (81.6%) and calls (92.9%) are the weak sources. Almost all of the gap is "phone not on any account": people who call or book a consult before they ever order. That is not a matching bug, so it is reported, not forced.

## Trade-offs

- **Strict vs loose matching.** Strict tiers miss a few real duplicates (recall 99.8%). Loose rules would merge strangers, and contacting a stranger with someone else's pet history is far worse than a split history.
- **Holdout size.** One run's holdout (about 100 people) is too small to prove an 8-point lift: the interval includes zero. Pool 4 to 6 weekly runs, or raise the holdout to 20% during the pilot.
- **Channel cost.** The agent cost (₹21,000 a run) eats most of the margin. A cheaper channel (WhatsApp) for `reorder_due` is the next thing to test.
- **Reach vs safety.** 29% of overdue customers are excluded. That is the price of the consent and DND rules, and the report shows it every run.

## Run it

Python 3.10+ and the standard library only. From the repo root:

```bash
python p4_customer_profile/gen_identity_data.py
```

```bash
python p4_customer_profile/build_profile.py
```

```bash
python p4_customer_profile/refill_outreach.py --purpose "weekly refill reminders"
```

```bash
python p4_customer_profile/test_profile.py
```

1. `gen_identity_data.py` builds `data/p4.db` (fixed seed).
2. `build_profile.py` builds the crosswalk, `customer_360` and the coverage report. If a build is held, it exits with the reason. Rerun with `--force` only when the drop is explained.
3. `refill_outreach.py` builds the list, exclusions and holdout, and writes the outreach report.
4. `test_profile.py` runs the checks.

## Files

| File | What it does |
| --- | --- |
| [gen_identity_data.py](gen_identity_data.py) | 5,000 synthetic pet parents across 9 source tables, with messy phones, second accounts, a shared clinic desk phone, duplicate clinic bookings, no-account calls and consults, and a pharmacy feed that lags. `truth_person` is the answer key, used only for scoring |
| [build_profile.py](build_profile.py) | Normalise, match in tiers, apply the guards, write `customer_xwalk`, `customer_360`, `match_candidates`, `clinic_duplicates` and the coverage report, and hold the build on a coverage drop |
| [refill_outreach.py](refill_outreach.py) | Overdue list, compliance filter, exclusion log, holdout, read-back, run record, lift report. Cost assumptions are in `ASSUME` at the top |
| [test_profile.py](test_profile.py) | Phone formats, desk phone not merged, oversized cluster sent to review, two numbers and two accounts = one customer, pincode + pet never merging, an end-to-end run (determinism, DND never listed, read-back) |
| [RUNBOOK.md](RUNBOOK.md) | Alerts, how to pause, who to tell, privacy checks, incident template |

## Choices beyond the spec

- **Foreign keys count as tier A.** An order or ticket linked by its account id joins at high confidence.
- **Shared-phone guard,** described above. The spec had only the size limit.
- **Tier D adds first name.** Pincode + pet alone gave thousands of pairs on 5,000 people.
- **Record tier is relative to the customer's anchor account** (the oldest one).
- **DND excludes refill reminders too,** until compliance rules on whether they count as transactional.

## Privacy and access

Raw phones stay in the source tables and `contact_lookup`. `customer_360` holds hashed ids and flags only. Pharmacy and consult detail appear only as aggregate flags (for example `has_chronic_medication`), never in a list.

```sql
GRANT SELECT ON customer_360, customer_xwalk, coverage_history TO cx_analyst;
GRANT SELECT ON contact_lookup, customer_360 TO outreach_list_job;  -- only the list job sees numbers
GRANT SELECT, INSERT ON outreach_runs, exclusions, outreach_assignments, outreach_list TO outreach_list_job;
```

## What I would do next

- Replace `simulate_outcomes()` with real 14-day orders and pool runs until the lift interval excludes zero.
- Add a review screen for tier C and D candidates, so an agent can accept or reject a pair in one click.
- Add a retention purge for `match_candidates` and `exclusions`, and an automatic cluster-size spike alert (the report shows the count today).
- Feed the profile to the bot ([P5](../p5_conversation_qa/README.md)) so a handover carries the customer's full history.
- Use the cluster view in [P1](../p1_refund_decisions/README.md) instead of P1's own account linking, so both projects agree on who a customer is.
