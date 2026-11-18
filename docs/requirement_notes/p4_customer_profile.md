# Requirement note: one customer profile and consent-safe refill outreach

_Synthetic numbers (5,000 planted people, data as of 15 Nov 2026). Outreach outcomes are simulated. Replace them with a one-week shadow list and real 14-day orders before sign-off._

**Problem.** The same pet parent appears under several accounts and phone numbers across orders, tickets, calls, pharmacy, vet consults and clinic bookings. So customer history is incomplete, outreach can reach the same person twice or reach someone who opted out on another number, and any measurement is undercounted. In the synthetic data, 1,068 of 5,001 customers have more than one phone.

**Proposed change.** Build a customer crosswalk (`customer_xwalk`) and a `customer_360` view that carry consent, DND and sensitivity flags but no raw sensitive fields. Publish a coverage report with every build. Use the profile first for a measured overdue-refill and reorder call list with a 10% holdout.

**Rules.**

| Rule | Value |
| --- | --- |
| Tier A match (high) | same normalised phone (last 10 digits), or a direct foreign key (order or ticket → account) |
| Tier B match (high) | same normalised email |
| Tier C match (medium) | same address hash + same pet name; merged, but counted separately in coverage |
| Tier D candidate | pincode + pet name + first name; **review only, never merged** |
| Never merge on | pincode alone, or pincode + pet alone |
| Shared phone guard | a number that is nobody's account phone but would join two accounts is never used to link (e.g. a clinic desk phone) |
| Cluster size limit | more than 5 accounts or 5 phones → sent to review |
| Who can be contacted | customers whose weakest link is tier A or B |
| Contact rules | consent on the channel, not DND, 09:00–21:00 IST, at most one contact per 7 days |
| List content | hashed customer id, first name, reason code, channel, call-by time, script id. **No medicine names.** |
| Holdout | 10% by hash of the customer id |

**Data.** Reads accounts, orders, tickets, calls, pharmacy prescriptions, vet consults, clinic bookings and consent. Writes `customer_xwalk`, `customer_360`, `match_candidates`, `cluster_review`, `shared_phones`, `unmatched_records`, `coverage_history`, `clinic_duplicates`, and for each list run `outreach_runs` (who, when, why), `exclusions`, `outreach_assignments` and `outreach_list`.

**Test plan.**
1. Fixtures for messy phone formats, a shared desk phone, an oversized cluster, two numbers and two accounts for one person, and pincode + pet never merging.
2. Determinism (same input, same ids) and a DND customer who must never be listed.
3. Read-back of the stored list against the exclusions before anything is sent.
4. One-week shadow list (built, not called).
5. Pilot on one segment with the 10% holdout.

**Success measure.**

| Measure | Target | Synthetic result |
| --- | --- | --- |
| Coverage per source | ≥ 90% on orders, tickets, pharmacy and bookings; reported for every source | 100% / 100% / 100% / 98.3%; calls 92.9%; consults 81.6% |
| Match precision vs a labelled sample | ≥ 99% | 100% (recall 99.8%) |
| Reorder lift vs holdout | positive, with the interval excluding zero (pool runs if needed) | +6.0 pts, 95% CI −3.5 to +15.4 (too few holdout customers in one run) |
| Opt-outs or complaints on contacted customers | below 1% | 0.6% |
| DND or no-consent customers on a list | 0 | 0 |

**Risks.**
- **Wrong merges contact a stranger.** Mitigation: strict tiers, the shared-phone guard, the cluster size limit, and tier D only ever goes to review (181 of its 182 synthetic candidates would have been wrong merges).
- **Missed merges** leave some history split. Accepted: missing a merge is cheaper than a wrong one.
- **Sensitive data in a list.** Mitigation: lists carry reason codes, never medicine names; pharmacy and consult detail appear only as aggregate flags.
- **A small holdout cannot prove the lift.** Mitigation: pool 4 to 6 weekly runs, or use a 20% holdout during the pilot.
- **A source change silently drops coverage.** Mitigation: a build is held if any source's coverage falls by more than 3 points.

**Rollback.** A held build keeps the previous profile. Stop the list job to stop outreach. See [the runbook](../../p4_customer_profile/RUNBOOK.md).

**Owner and sign-off.** The manager signs off. Compliance reviews consent, DND and sensitivity handling. The pharmacy owner approves the reminder script. CX analytics owns the profile, the coverage report and the runbook.

| | Name | Date |
|---|---|---|
| Analyst (owner) | | |
| Manager | | |
| Compliance | | |
| Pharmacy owner | | |
