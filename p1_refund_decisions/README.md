# Project 1: Refund and claim decision system

_Synthetic data, same structure as production. No real customer data, thresholds or fraud rules._
Spec: [../projects/01-refund-claim-decision-system.md](../projects/01-refund-claim-decision-system.md) · Full results: [out/report.md](out/report.md) · Requirement note: [../docs/requirement_notes/p1_refund_decisions.md](../docs/requirement_notes/p1_refund_decisions.md) · Runbook: [RUNBOOK.md](RUNBOOK.md)

## Problem
Agents decide every refund or replacement claim by hand, including obvious low-risk ones. One person can claim on several accounts, so per-account history undercounts them. Nobody can say how many wrong approvals or wrong declines the current process makes, or what a different limit would do.

## Approach
1. **Link accounts.** Normalise phone numbers (last 10 digits) and addresses (lowercase, collapsed spaces, hashed with the pincode). Merge accounts on **exact** links (same phone or email) and **strong** links (same address plus the same pet or card). A **weak** link (same address, nothing else in common, like flatmates) is never merged; it only flags the claim for an agent.
2. **Score and decide.** Every claim gets a 0–1 risk score from four weighted parts (cluster claims in 90 days, cluster refund rate, evidence against the claim, claim as a share of the order value). Versioned rules then auto-approve, auto-decline or route the claim to an agent. The thresholds live in [rules/v1.json](rules/v1.json), so an analyst can change the limit, the claim count, the reasons that auto-approve and the holdout share without touching code.
3. **Log every decision** with its score, reason codes and rule version. Each row is read back after it is written, and the run fails if any claim in the window has no log row.
4. **Holdout.** `md5(claim_id) % 100 < 10` skips the rules and goes to an agent as today.
5. **Measure both sides.** The threshold sweep re-runs the same engine at each limit. The weekly report covers wrong approvals and wrong declines from the audit sample, agent minutes saved, a holdout-vs-treated comparison and drift flags.

## Result (13-week test window, 4,283 claims)
| Measure | Value |
|---|---|
| Auto-decision rate at ₹1,500 | 55% (2,073 approved, 49 declined) |
| Wrong approvals | 15 audited (2.5% of audited approvals), ~₹39k over the window |
| Wrong declines | 2 audited (10% of audited declines, at the guardrail) |
| Agent time saved | ~33 agent-hours a week |
| Net weekly saving vs holdout | **₹11,547** |
| Caught only by the cluster view | 5 claims (2 audited not genuine) |

## Trade-off
Net saving is flat from ₹500 to ₹1,500 and falls above that. Going from ₹1,500 down to ₹1,000 gains ₹394 a week, but 91 more claims (26 of the 29 audited are genuine) would wait for an agent. Going up to ₹2,000 loses ₹610 a week to wrong approvals. ₹1,500 is the highest limit before net starts to fall.

Wrong declines are few but sit at the 10% guardrail. Both are honest heavy buyers (10 and 16 orders) with three claims in 90 days. The decline rule counts claims, not claims per order, so a loyal customer looks like an abuser. That is the next rule to fix.

## What I would do next
- Move the holdout to the cluster level: 31% of holdout claims share a cluster with a treated claim.
- Audit 100% of auto-declines, not 30%. They are few and the costliest mistake.
- Rule v2: decline on claims per order in 90 days, not the raw claim count, so heavy honest buyers stop tripping it. Test it with the same sweep before it ships.
- Replace the synthetic `agent_decisions` table with the real agent outcomes and rerun the sweep.

## Run it
Python 3.10+ and the standard library only (sqlite3). Run these from the repo root:
```bash
py p1_refund_decisions/gen_data.py
```
```bash
cd p1_refund_decisions && py engine.py && py report.py && py -m unittest
```
Set `PYTHONIOENCODING=utf-8` if a Windows console garbles ₹. To use a different database, set `P1_DB`.

## Files
| File | What it is |
|---|---|
| `gen_data.py` | Seeded synthetic accounts, orders, claims, audit sample and today's agent decisions, with planted patterns |
| `engine.py` | Account linking, features, score, rules, decision log with read-back, rule-version registry |
| `rules/v1.json` | The versioned rule config. A change means a new file with a new `rule_version`; the engine refuses to register an edited version |
| `report.py` | Threshold sweep and chart, holdout comparison, weekly report, results table, editable assumptions |
| `test_engine.py` | One test per rule branch, boundary amounts, the flatmates case, read-back, version immutability |
| `out/` | Generated `report.md` and `sweep.svg` |

## Deviations from the spec, on purpose
- **SQLite and JSON, not DuckDB and YAML.** This keeps the project on the standard library only. SQLite has no `regexp_replace`, so phone and address normalisation is done in Python before linking.
- **Pincode-only is not a weak link.** With real pincodes it would flag half a city. Weak means the same address with nothing else in common.
- **The sweep re-runs the engine instead of using the score-cut SQL.** That way it measures what the rules actually do, not a proxy for them.
- **The decline rule has its own amount cap (₹5,000).** Otherwise the approval limit would also move declines, and the sweep would mix two decisions.
- **CSAT is not modelled**, because the synthetic data has no survey responses.
