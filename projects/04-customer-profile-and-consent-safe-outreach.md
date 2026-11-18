# Project 4: One Customer Profile and Consent-Safe Outreach

> Synthetic data, same structure as production. No real customer, prescription or pet-health data.

## 1. At a glance

| | |
| --- | --- |
| **Problem** | A pet parent appears under several phone numbers and accounts, across orders, tickets, calls, pharmacy prescriptions, vet consults and clinic bookings, each with its own id. Nobody can see one history, and outreach cannot be targeted or measured. |
| **What I build** | A cross-system `customer_id` crosswalk with a coverage report, a `customer_360` view with consent and sensitivity flags, then the first consumer: an overdue-refill outreach list with a compliance filter and a holdout. |
| **Headline measure** | Join coverage per source (for example "calls matched 91%, consults matched 74%, ambiguous 3%") and lift in reorders for contacted versus holdout, in rupees. |
| **Fix owner** | CX analytics (profile), retention and pharmacy (outreach), clinics (bookings). |
| **JD lines** | One customer, many identities; say how much each join covers; customer profile; outreach systems and daily lists; consent, DND and sensitive data; holdouts; clinic duplicate bookings. |

## 2. Problem at the company

- Pet parents use more than one phone number, and some create more than one account.
- Orders, pharmacy, vet consults, clinic bookings, calls and tickets live in different systems with different ids.
- Several Bangalore clinics take bookings, and the same person can hold more than one slot using different numbers, which wastes slots.
- Pharmacy customers who are overdue for a refill are not reached in time, and outreach is not measured.

## 3. Goals and non-goals

**Goals**

- Build one customer id across all sources and say how much each join covers.
- Keep match confidence, so uncertain matches are never treated as certain.
- Carry consent, DND and sensitivity flags on the profile.
- Use the profile to produce a safe, measured outreach list.

**Non-goals**

- No probabilistic or machine-learning matching in v1. Deterministic tiers are easier to explain and audit.
- No clinical content in outreach. Reminders use a reason code, not a diagnosis or medicine name.

## 4. Source tables (synthetic)

| Source | Identity fields available | Notes |
| --- | --- | --- |
| `accounts` | account_id, phone, email, pincode | multiple accounts per person possible |
| `orders` | account_id, shipping phone, address | phone may differ from account phone |
| `tickets` | ticket_id, order_id (nullable), phone | about 4% have no order id |
| `calls` | phone only | no account id |
| `pharmacy_rx` | account_id, pet_id, medicine_code, refill_days, last_fill_date | sensitive |
| `vet_consults` | phone, pet_id | sensitive |
| `clinic_bookings` | phone, clinic_id, slot time | duplicates across numbers |
| `consent` | customer_id or phone, channel, consent_status, dnd_flag | |

**Planted patterns:** people with two numbers; phone formats such as `+91 98xxx`, `098xxx`, spaces and dashes; the same pet booked at two clinics under two numbers; consult records with no account id.

## 5. Matching method

**Step 1: normalise**

```sql
-- DuckDB
SELECT source, source_id,
       right(regexp_replace(phone, '[^0-9]', '', 'g'), 10) AS phone_norm,
       lower(trim(email))                                   AS email_norm
FROM all_identity_fields;
```

Normalise phones to the last 10 digits, lower-case emails, trim names, and strip punctuation from addresses.

**Step 2: link in tiers**

| Tier | Rule | Confidence |
| --- | --- | --- |
| A | same normalised phone | high |
| B | same email | high |
| C | same address hash and same pet name | medium |
| D | same pincode and same pet name | low: kept as a candidate, not merged |

**Step 3: cluster.** Build connected components over tier A, B and C links. Each cluster gets a `customer_id`. Tier D pairs go to a `match_candidates` table for review and are never merged automatically.

**Step 4: crosswalk**

```sql
CREATE TABLE customer_xwalk (
  customer_id TEXT, source TEXT, source_id TEXT, match_tier TEXT, linked_at TIMESTAMP
);
```

One row per source record. A record can only belong to one customer.

**Step 5: guard against over-merging.** Check cluster sizes. A cluster with more than a set number of accounts or phones (for example more than 5) is flagged for review instead of accepted, because a shared number such as a clinic desk phone can glue unrelated people together.

## 6. Coverage report (say how much each join covers)

```sql
SELECT source,
       COUNT(*)                                         AS records,
       COUNT(*) FILTER (WHERE x.customer_id IS NOT NULL) AS matched,
       ROUND(100.0 * COUNT(*) FILTER (WHERE x.customer_id IS NOT NULL) / COUNT(*), 1) AS matched_pct,
       COUNT(*) FILTER (WHERE x.match_tier = 'C')        AS medium_confidence
FROM source_records s
LEFT JOIN customer_xwalk x USING (source, source_id)
GROUP BY source ORDER BY matched_pct;
```

Publish this table with every release of the profile. Also report: number of customers with more than one phone, clusters flagged for review, and the count of tier D candidates waiting.

| Source | Records | Matched % | Medium-confidence | Unmatched reason |
| --- | --- | --- | --- | --- |
| orders | | | | |
| tickets | | | | missing order id and phone |
| calls | | | | phone not in any account |
| pharmacy | | | | |
| vet consults | | | | |
| clinic bookings | | | | |

## 7. The `customer_360` view

One row per `customer_id`, with only the fields teams need:

| Group | Fields |
| --- | --- |
| Identity | customer_id, number of phones, number of accounts, first seen |
| Orders | order count, last order date, average order value, usual reorder gap in days |
| Service | tickets in 90 days, calls in 90 days, last contact date, last CSAT |
| Pharmacy | has chronic medication (yes or no), next refill due date |
| Care | consults in 12 months, clinic visits in 12 months |
| Compliance | consent by channel, DND flag, sensitivity flag, do-not-contact reason |
| Quality | match tier mix, coverage flags |

Raw phone numbers and emails stay in the source tables. The view carries hashed ids; a controlled lookup returns a number only for an approved list run.

## 8. Clinic duplicate bookings

- Group bookings by customer cluster, pet and week.
- Flag a cluster with more than one active booking for the same pet and specialty in the same week.
- Tier C matches (same address and pet) are flagged, not cancelled. An agent confirms with the customer.
- Metric: duplicate slots found per week, slots freed after confirmation, and genuine customers wrongly contacted.

## 9. First consumer: overdue-refill outreach

**Who is overdue.** From pharmacy data: `next_refill_due = last_fill_date + refill_days`. Overdue means today is more than 3 days past that date and no order has been placed since. For non-medicine customers, overdue means days since last order is more than 1.5 times the customer's own usual gap.

```sql
-- usual gap per customer with window functions
WITH gaps AS (
  SELECT customer_id, order_date,
         order_date - LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date) AS gap_days
  FROM orders_by_customer)
SELECT customer_id, median(gap_days) AS usual_gap
FROM gaps WHERE gap_days IS NOT NULL GROUP BY customer_id;
```

**Compliance filter (before any list leaves).** Every exclusion is written to `exclusions` with its reason.

1. Consent for the chosen channel.
2. Not on DND for promotional content; refill reminders are treated carefully and checked with compliance.
3. Allowed hours only (09:00 to 21:00 IST, to be confirmed).
4. Contact cap (for example one reminder per customer per 7 days).
5. Sensitivity: the list shows a reason code such as `refill_due`, never the medicine name or diagnosis.
6. Match quality: only tier A and B matches are contacted; tier C goes to review.

**Holdout.** 10% of eligible customers, chosen by `hash(customer_id) % 10 = 0`, are not contacted.

**Measurement.** Reorder within 14 days for contacted versus holdout. Lift in percentage points times average order margin, minus agent cost, equals net value. Report opt-out rate and complaints next to the lift.

| Item | Contacted | Holdout | Difference |
| --- | --- | --- | --- |
| Eligible customers | | | |
| Reordered within 14 days | | | |
| Revenue and margin (₹) | | | |
| Opt-outs or complaints | | | |
| Agent minutes and cost (₹) | | | |

**List columns for the team:** customer_id (hashed), first name only, reason code, preferred channel, call-by time, script id. Nothing else.

## 10. Testing

1. Fixtures for phone formats: `+91 98765 43210`, `098765 43210`, `98765-43210`, `919876543210` must all normalise to the same value.
2. A fixture where two unrelated people share a clinic desk number: the cluster must be flagged, not merged.
3. A person with two numbers and two accounts: must become one `customer_id`.
4. Consent test: a customer with DND must appear in `exclusions` and never in the list.
5. Read back: write the list, query it, compare to the filter output.
6. Re-run determinism: same input, same `customer_id` assignments.

## 11. Monitoring and runbook

| Signal | Check | Action |
| --- | --- | --- |
| Coverage drops by more than 3 points | each build | hold the release, find the source change |
| Cluster size spike | each build | review shared numbers |
| Exclusion share jumps | each list | check the consent feed |
| Opt-out rate above limit | weekly | pause the script and review the message |

## 12. Privacy and access

- Pharmacy and consult fields are sensitive; they are visible only in the aggregate profile flags (`has_chronic_medication`), not in lists.
- Least-privilege roles: analysts read the profile view; only the list job reads the lookup table with phone numbers.
- Retention: keep match candidates and exclusion logs for a defined period, then delete.
- Every list run records who ran it, when and for what purpose.

## 13. Requirement note (ready to copy)

**Problem.** The same pet parent appears under several ids and numbers across systems, so history, outreach and measurement are incomplete.

**Proposed change.** Build a customer crosswalk and `customer_360` view with consent and sensitivity flags; use it to generate a measured overdue-refill list.

**Rules.** Match on normalised phone and email, then address plus pet name; never merge on pincode alone. Contact only matched customers with consent, inside allowed hours, within the contact cap.

**Data.** accounts, orders, tickets, calls, pharmacy, consults, clinic bookings, consent.

**Test plan.** Fixtures for formats and shared numbers, consent tests, read-back of the stored list, one-week shadow list, pilot on one segment with a 10% holdout.

**Success measure.** Coverage above __% on each source and a measured lift in reorders versus holdout with opt-outs below __%.

**Owner and sign-off.** Manager signs off; compliance reviews consent and sensitivity handling.

## 14. Deliverables checklist

- [ ] Synthetic multi-source identity data with messy phones
- [ ] Normalisation and tiered matching SQL
- [ ] `customer_xwalk`, `match_candidates` and review queue
- [ ] Coverage report with every build
- [ ] `customer_360` view and access roles
- [ ] Clinic duplicate booking detector
- [ ] Overdue list with compliance filter, exclusion log, holdout
- [ ] Lift report and weekly summary
- [ ] Runbook, requirement note, README

## 15. Results (fill in after running)

| Measure | Value |
| --- | --- |
| Customers found with more than one phone | |
| Coverage per source | |
| Clusters flagged for review | |
| Duplicate clinic slots found per week | |
| Reorder lift versus holdout and net value (₹) | |

## 16. Risks and trade-offs

- Strict matching misses some real duplicates; loose matching merges strangers. Tiers and a review queue balance the two.
- Fewer contacts protect customers but reduce reach; the opt-out rate and lift show the balance.
- A richer profile is more useful but raises privacy risk; flags replace raw sensitive data.

## 17. What I would do next

- Add a human review screen for tier C and D candidates.
- Feed the profile to the bot so handover carries full history.
- Add customer states (new, active, drifting, lapsed) for retention journeys.

## 18. Interview talking points

- "I always say how much each join covers; a number without its coverage is not trustworthy."
- "Uncertain matches are candidates, not facts. Only high-confidence links drive outreach."
- "Lists carry reason codes, never medicine names."
