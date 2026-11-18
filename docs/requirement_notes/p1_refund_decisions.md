# Requirement note: auto-settle low-risk refund and replacement claims

_Synthetic numbers from the 13-week test window. Replace them with numbers from a shadow run on real data before sign-off._

**Problem.** Refund and replacement claims are decided by hand: about 12 agent minutes per claim, including obvious low-risk ones. Repeat claimers who use several accounts are not visible, because agents see one account at a time.

**Proposed change.** Auto-settle low-risk claims with a transparent, versioned rule set. Link the accounts of one person. Send everything else to an agent. Keep a 10% holdout.

**Rules.**
- **Auto-approve** up to **₹1,500** when the customer's linked accounts have at most **1** claim in the last 90 days, the risk score is below 0.3, and the delivery evidence supports the reason (a late claim on an order that really was late, or a damaged claim with a delivery photo).
- **Auto-decline** only a missing-parcel claim with an OTP-verified delivery, from a customer with **3+** claims in 90 days across exact or strong links, up to ₹5,000.
- **Weak identity matches never decide a claim.** Everything else goes to an agent.

**Data.** Reads claims, orders (delivery time, promised date, proof), accounts and account_links. Writes only to decision_log (ids, score, reason codes, rule version; no names, phone numbers or addresses).

**Test plan.**
- Unit tests for every rule branch, run on every pull request.
- A labelled set of test claims that includes flatmates sharing one address.
- A one-week shadow run with no action taken, compared with agent decisions.
- A one-week pilot on 5% of claims, low-value only, with every decision read back after it is written.

**Success measure, measured against the holdout.**
- Net weekly saving above **₹8,000** (synthetic result: ₹11,547).
- Wrong-approval rate under **8%** (synthetic: 2.5%).
- Wrong-decline rate under **10%** (synthetic: 10%, at the limit; see risks).

**Risks.**
- **Genuine customers wrongly declined.** In the synthetic run both wrong declines were heavy, honest buyers. Mitigations: weak links never decline, every wrong decline is reviewed weekly, all auto-declines are audited, and v2 counts claims per order instead of raw claims.
- **Wrong approvals above ₹1,500** cost more than the agent time they save; the sweep shows net falling above that limit.
- **Holdout contamination:** 31% of holdout claims share a customer with a treated claim. Move to a cluster-level holdout before reading a final result.

**Rollback.** Run the previous rules file, or a new version with `"enabled": false`. Details are in the runbook.

**Owner and sign-off.** The manager signs off before this goes to Product. Finance agrees the ₹1,500 limit. The CX lead owns the rules and the Monday review.

| | Name | Date |
|---|---|---|
| Analyst (owner) | | |
| Manager | | |
| Finance | | |
