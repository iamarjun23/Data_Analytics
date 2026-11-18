# Project 1: Refund and Claim Decision System

> Synthetic data, same structure as production. No real customer data, thresholds or fraud rules.

## 1. At a glance

| | |
| --- | --- |
| **Problem** | Refund and replacement claims are decided by hand. A loose rule leaks money (including people who claim again and again across several accounts); a tight rule slows genuine customers. |
| **What I build** | A rules scorer that auto-approves, auto-declines or routes to an agent; account linking; a decision log; a 10% holdout; a threshold sweep; a weekly report; a one-page requirement note. |
| **Headline measure** | At the chosen limit: rupees saved, rupees lost to wrong approvals, genuine customers wrongly declined, agent minutes saved. |
| **Fix owner** | Finance (limit) and CX (rules and review). |
| **JD lines** | Decision systems; every rule cuts both ways; measuring what a system really did; log every automatic decision; weekly review of wrong approvals and wrong declines; requirement notes; test on a small slice and read back. |

## 2. Problem at the company

- Customers raise claims for late, missing, wrong or damaged orders.
- The same person can hold several accounts (different phones, same address or pincode) and claim on each, so per-account history under-counts them.
- Agents decide every claim the same slow way, even obvious low-risk ones.
- Nobody can say how many wrong approvals or wrong declines the current rule produces, or what a change in limit would do.

## 3. Goals and non-goals

**Goals**

- Auto-settle low-risk claims; send everything else to a person.
- Count a customer's claim history across linked accounts.
- Log every automatic decision with its reason and rule version.
- Measure both sides of every rule every week.
- Measure real impact with a holdout, not an assumption.

**Non-goals**

- No machine-learning model in v1. A transparent rules score is easier to explain, test and audit.
- No automatic decline on a weak identity match. Weak matches go to a person.
- No change to the live rule without a signed requirement note and a pilot slice.

## 4. Data (synthetic)

| Table | Purpose | Key fields |
| --- | --- | --- |
| `claims` | Each claim | claim_id, order_id, account_id, amount, reason, created_at_utc |
| `orders` | Order context | order_id, value, delivered_ts_utc, promised_date, status, delivery_proof (otp_ok, photo_ok, none) |
| `accounts` | Identity fields | account_id, phone_norm, address_hash, pincode |
| `account_links` | Which accounts are one person | account_id, cluster_id, match_tier (exact, strong, weak) |
| `claim_audit` | Ground truth on a sample | claim_id, is_genuine |
| `decision_log` | Every decision | decision_id, claim_id, decided_at_utc, rule_version, score, decision, reason_codes, is_holdout |
| `rule_versions` | Rule config history | rule_version, config_yaml, approved_by, approved_at |

**Planted patterns in the synthetic data:** about 3% of claims come from clusters of two to four accounts; genuine late-delivery claims are common on a few lanes; damaged claims with a photo are mostly genuine; a small share of claims are repeat claimers with no delivery issue.

## 5. Account linking

Normalise identity fields first, then link in tiers.

```sql
-- DuckDB: last 10 digits of the phone, digits only
SELECT account_id,
       right(regexp_replace(phone, '[^0-9]', '', 'g'), 10) AS phone_norm,
       md5(lower(trim(address_line)) || '|' || pincode)    AS address_hash
FROM accounts;
```

| Tier | Rule | Use |
| --- | --- | --- |
| Exact | same normalised phone or same email | merge into one cluster |
| Strong | same address hash and same pet name or same payment fingerprint | merge, show in decision reason |
| Weak | same pincode only, or same address with a different surname | do **not** merge; flag for an agent |

Build clusters as connected components over exact and strong links. Store `cluster_id` and the tier that created each link. A family sharing one address must not look like an abuser, which is why weak links never decide a claim on their own.

## 6. Rules engine

**Features per claim**

- `claim_amount`, `order_value`
- `claims_90d_cluster`: claims by the whole cluster in the last 90 days
- `refund_rate_cluster`: refunded value over ordered value for the cluster, 180 days
- `delivered_on_time`: yes or no, from the delivery data
- `delivery_proof`: OTP verified, photo, or none
- `reason`: late, missing, wrong item, damaged

**Decision logic (example config, stored as YAML and versioned)**

```yaml
rule_version: v1
auto_approve:
  max_amount: 1500
  max_claims_90d_cluster: 1
  require_any:
    - reason: late
      delivered_on_time: false
    - reason: damaged
      delivery_proof: photo
auto_decline:
  - reason: missing
    delivery_proof: otp_ok
    min_claims_90d_cluster: 3
    only_if_link_tier_at_least: strong
route_to_agent:
  - amount_above: 1500
  - any_weak_link: true
  - everything_else: true
holdout_share: 0.10
```

**Risk score** (shown in the log so agents see why): a simple weighted sum of the features, scaled 0 to 1. Weights live in the same YAML. Keep it small enough to explain in a minute.

**Settings an analyst can change without code:** amount limit, claim-count limit, which reasons auto-approve, holdout share. Each change creates a new `rule_version` with an approver and a date.

## 7. Holdout design

- A claim is in the holdout if `hash(claim_id) % 10 = 0`. This is random, repeatable and cannot be gamed by agents.
- Holdout claims skip the rules and go to an agent exactly as they do today.
- Compare treated and holdout on: handling time, approval rate, refund rupees, repeat-claim rate within 30 days, and CSAT.
- Caveat to state: the same customer cluster can have a holdout claim and a treated claim. Report the share and, if large, assign holdout by `cluster_id` instead.
- Minimum size: check with the sizing note in the extension kit (baseline rate, effect to detect, weekly claim volume) before promising a result.

## 8. Measurement: both sides, every week

**Definitions**

- **Wrong approval:** auto-approved, and the audit says not genuine. Cost = claim amount.
- **Wrong decline:** auto-declined, and the audit says genuine. Cost = agent time to fix + goodwill credit + lost repeat margin (assumption table).
- **Saved:** agent minutes saved times cost per minute, plus the amount of correctly declined claims.
- **Net:** saved minus cost of wrong approvals minus cost of wrong declines.

**Threshold sweep (what the manager and Finance decide on)**

```sql
WITH limits AS (SELECT unnest([500, 1000, 1500, 2000, 3000]) AS auto_limit),
scored AS (
  SELECT c.claim_id, c.amount, d.score, a.is_genuine
  FROM claims c
  JOIN decision_log d USING (claim_id)
  JOIN claim_audit a  USING (claim_id)
  WHERE d.is_holdout = FALSE
)
SELECT l.auto_limit,
       COUNT(*) FILTER (WHERE s.amount <= l.auto_limit AND s.score < 0.3)                         AS auto_approved,
       SUM(s.amount) FILTER (WHERE s.amount <= l.auto_limit AND s.score < 0.3 AND NOT s.is_genuine) AS wrong_approval_rs,
       COUNT(*) FILTER (WHERE s.score >= 0.8 AND s.is_genuine)                                    AS wrong_declines
FROM limits l CROSS JOIN scored s
GROUP BY l.auto_limit ORDER BY l.auto_limit;
```

Plot approval rate, wrong-approval rupees and wrong declines against the limit. Mark the chosen point and say why.

**Weekly report (template)**

| Item | This week | Last week | Assumed at approval |
| --- | --- | --- | --- |
| Claims received | | | |
| Auto-approved / auto-declined / to agent | | | |
| Wrong approvals (count, rupees) | | | |
| Wrong declines (count) | | | |
| Agent minutes saved | | | |
| Net saving (rupees) | | | |
| Holdout vs treated: handling time, repeat claims | | | |
| Drift flags and actions taken | | | |

## 9. Testing and rollout

1. **Unit tests:** one fixture per rule branch (approve, decline, route, weak link, boundary amounts). Run on every pull request.
2. **Test records:** a labelled set of synthetic claims, including edge cases such as a family sharing an address.
3. **Read back:** after the engine writes a decision, query it back and compare fields to the input (claim_id, rule_version, reason codes).
4. **Shadow run:** score real-shaped claims for one week without acting; compare with agent decisions.
5. **Pilot slice:** 5% of claims, low-value only, for one week.
6. **Rollout gate:** wrong-decline rate under the agreed limit and net saving positive on the pilot.
7. **Rollback:** switch to the previous `rule_version` or turn off auto-decisions. Keep a backup of the config before every change.

## 10. Monitoring and drift

| Signal | Check | Action |
| --- | --- | --- |
| Auto-approve share | daily, versus 4-week average | alert if it moves more than 5 points |
| Wrong-approval rate on the audit sample | weekly | pause auto-approve if above limit |
| Share routed to agents | daily | alert on sudden fall (rules too loose) or rise (data problem) |
| Decision log gaps | hourly | any claim without a log row is a failure |
| New reason values | per run | send to an UNMAPPED bucket and alert |

Runbook headings: how to pause the engine, who to tell, how to find the cause (rule change, data change, real behaviour change), how to write the fix down, named second on call.

## 11. Privacy and security

- The decision log stores ids and reason codes, not names, phone numbers or addresses.
- Identity fields are hashed for linking; raw values stay in the source system.
- Least-privilege read access to source tables; write access only to `decision_log`.
- No secrets in code; credentials from environment variables.
- Every automatic decision is explainable from its logged reason codes.

## 12. Requirement note (ready to copy)

**Problem.** Refund and replacement claims are decided manually. Handling time is high, and repeat claimers across accounts are not visible.

**Proposed change.** Auto-settle low-risk claims by a transparent rule set, link accounts of one person, send everything else to an agent, keep a 10% holdout.

**Rules.** Auto-approve up to ₹__ when claims in 90 days across linked accounts are at most __ and the delivery evidence supports the reason. Auto-decline only on strong links and clear delivery proof. Everything else to an agent.

**Data.** claims, orders, delivery events, accounts, account_links. Output written to `decision_log`.

**Test plan.** Unit tests per rule branch, labelled test claims, one week shadow run, one week 5% pilot with read-back checks.

**Success measure.** Net weekly saving above ₹__ with wrong-approval rate under __% and wrong-decline rate under __%, measured against the holdout.

**Risks.** Genuine customers wrongly declined; mitigation: weak links never decline, weekly review of every wrong decline.

**Owner and sign-off.** Manager signs off before the note goes to Product. Finance agrees the limit.

## 13. Deliverables checklist

- [ ] Synthetic claims, orders and audit sample generator (seeded)
- [ ] Account linking SQL and cluster table
- [ ] Rules engine with versioned YAML config
- [ ] `decision_log` writer with read-back check
- [ ] Threshold sweep query and chart
- [ ] Weekly report generator (markdown or sheet)
- [ ] Unit tests and one runnable demo
- [ ] Runbook and incident note template
- [ ] Requirement note
- [ ] README with problem, approach, result, trade-off, next step

## 14. Results (fill in after running)

| Measure | Value |
| --- | --- |
| Claims in the test window | |
| Auto-decision rate at chosen limit | |
| Wrong approvals (count, ₹) | |
| Wrong declines (count) | |
| Net weekly saving (₹) versus holdout | |
| Claims caught only by cluster view (not by per-account view) | |

## 15. Risks and trade-offs

- **Looser limit:** more agent time saved, more wrong approvals.
- **Tighter limit:** fewer wrong approvals, more genuine customers waiting.
- **Account linking:** catches repeat claimers, but a wrong merge hurts a real customer; tiers and the no-auto-decline-on-weak-link rule limit this.
- **Audit sample bias:** ground truth comes from sampled claims; refresh it every month.

## 16. What I would do next

- Replace the rule score with a calibrated model once there are enough labelled claims, and keep the rules as a safety net.
- Add the circuit breaker from Project 5 to pause auto-decisions on drift.
- Extend the same pattern to returns and exchanges.

## 17. Interview talking points

- "I measured both wrong approvals and wrong declines, because a rule that only saves money is half the story."
- "The holdout is what lets me say how much we saved, not how much we declined."
- "Weak identity links never decide a claim. They go to a person."
