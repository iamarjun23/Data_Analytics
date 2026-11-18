# P4: One customer profile and consent-safe outreach

Spec: [projects/04-customer-profile-and-consent-safe-outreach.md](../projects/04-customer-profile-and-consent-safe-outreach.md). The requirement note (section 13) and runbook (section 11) are there.
**All data is synthetic.** Python stdlib + SQLite only, no installs.

```
py p4_customer_profile/gen_identity_data.py                                   # data/p4.db, fixed seed
py p4_customer_profile/build_profile.py                                       # crosswalk, customer_360, coverage_report.md
py p4_customer_profile/refill_outreach.py --purpose "weekly refill reminders" # list, exclusions, holdout, outreach_report.md
py p4_customer_profile/test_profile.py                                        # spec section 10 checks
```

| File | What it does |
| --- | --- |
| `gen_identity_data.py` | 5,000 synthetic pet parents across 9 source tables, with messy phones, second accounts, a shared clinic desk phone, duplicate clinic bookings, no-account calls and consults, and a pharmacy feed that lags. `truth_person` is the answer key, used only for scoring. |
| `build_profile.py` | Normalises the records, matches them in tiers A/B/C, and puts tier D pairs in `match_candidates`. Also: the shared-phone and cluster-size guards, `customer_xwalk`, `customer_360`, `clinic_duplicates`, the coverage report, and the coverage-drop hold. |
| `refill_outreach.py` | Builds the overdue list (refill_due / reorder_due). Applies DND, consent, contact cap and match quality, and logs every exclusion. Holds back 10% by hash, reads the list back, records who ran it, and writes the CSV and lift report. |
| `test_profile.py` | Checks phone formats, desk phone not merged, oversized cluster sent to review, two numbers / two accounts = one customer, pincode+pet never merging, determinism, DND never listed, read-back. |

## Results (synthetic, data as of 2026-11-15)

| Measure | Value |
| --- | --- |
| Customers found with more than one phone | 1,068 of 5,001 |
| Coverage per source | consults 81.6%, calls 92.9%, clinic bookings 98.3%, orders / tickets / rx 100% ([coverage_report.md](coverage_report.md)) |
| Match quality vs planted truth | 100% pair precision, 99.8% recall (1 pair missed: second account linked by nothing) |
| Clusters flagged for review | 0 over size limit; 1 shared phone (the clinic desk) blocked from linking, its 12 walk-in bookings left unmatched |
| Tier D candidates waiting | 182. Only 1 is really the same person (the missed pair above): 181 would be wrong merges, which is why tier D only ever goes to review |
| Duplicate clinic slots found per week | 8 / 7 / 10 (weeks 46-48), 0 groups that were really two people |
| Outreach funnel | 1,382 overdue -> 405 excluded (260 no consent, 116 DND, 28 contact cap, 1 match review) -> 875 called, 102 holdout |
| Reorder lift vs holdout and net value | +6.0 pts, net Rs 3,606 per run on **simulated** outcomes; the 95% interval (-3.5 to +15.4) includes zero |

Trade-off: one run's holdout (about 100 people) is too small to prove an 8-point lift. Pool 4 to 6 weekly runs, or raise the holdout to 20% during the pilot. The agent cost (Rs 21,000) eats most of the margin, so a cheaper channel (WhatsApp) for reorder_due is the next thing to test.

## Choices beyond the spec

- **Foreign keys count as tier A.** An order, ticket or rx linked by its account_id or order_id joins at high confidence.
- **Shared-phone guard.** A number that is nobody's account phone but would join two accounts is never used to link. This catches the clinic desk phone even when only two people share it; the size limit alone would not.
- **Tier D is pincode + pet name + first name.** Pincode + pet alone gave thousands of pairs on 5,000 people.
- **Record tier is relative to the customer's anchor account** (the oldest one). A customer's `match_quality` is the weakest tier among its accounts, and only A/B are contacted.
- **DND excludes refill reminders too,** until compliance rules on transactional reminders.

## Access (production roles)

Raw phones stay in the source tables and `contact_lookup`. `customer_360` holds hashed ids and flags only.

```sql
GRANT SELECT ON customer_360, customer_xwalk, coverage_history TO cx_analyst;
GRANT SELECT ON contact_lookup, customer_360 TO outreach_list_job;  -- only the list job sees numbers
GRANT SELECT, INSERT ON outreach_runs, exclusions, outreach_assignments, outreach_list TO outreach_list_job;
```

Not built yet: a retention purge for `match_candidates` / `exclusions`, and an automatic cluster-size spike alert (the report shows the count). Add both when this runs on a schedule.
