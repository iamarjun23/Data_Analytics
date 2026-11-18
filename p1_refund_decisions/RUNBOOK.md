# Runbook: refund and claim decision engine

## Monitoring (from `report.py`, drift flags section)
| Signal | Check | Action |
|---|---|---|
| Auto-approve share | weekly vs 4-week average (daily once live) | alert if it moves more than 5 pts (`guardrails.auto_share_move_pts`) |
| Wrong-approval rate on audit sample | weekly | above `max_wrong_approval_rate` → pause auto-approve |
| Wrong-decline rate on audit sample | weekly | above `max_wrong_decline_rate` → review every decline, consider pausing declines |
| Share routed to agents | weekly vs 4-week average | a sudden fall means the rules are too loose; a sudden rise usually means a data problem |
| Decision log gaps | every run | the engine refuses to finish if any claim has no log row |
| New reason values | every run | routed with `UNMAPPED_REASON` and flagged |

## How to pause the engine
1. Copy the live rules file to a new version (for example `rules/v1_1.json`). Set `"rule_version": "v1.1"` and `"enabled": false`, and fill in `approved_by` and `approved_at`.
2. Run `py engine.py rules/v1_1.json`. Every claim now goes to an agent with reason `ENGINE_PAUSED`.
3. To pause only auto-approve, set `auto_approve.max_amount` to 0. To pause only auto-decline, set `auto_decline` to `[]`.

## Rollback
Run the previous rules file: `py engine.py rules/v1.json`. Rule files are never edited in place; the engine refuses to register a changed config under an existing version, so every past config is its own backup.

## Who to tell
- **Finance** owns the amount limit.
- **The CX lead** owns the rules and the review.
- **Second on call:** _name_.
- Post in the CX ops channel with the template below within 30 minutes of pausing.

## Finding the cause
1. **Rule change?** Check `SELECT * FROM rule_versions ORDER BY approved_at DESC` and compare the configs.
2. **Data change?** Look for new reason values, missing delivery proof, a jump in `NO_RULE_MATCHED`, or a link table that suddenly has far more or fewer clusters.
3. **Real behaviour change?** Look at the reason mix, the lanes and the clusters behind this week's wrong approvals.

Read the reason codes of the affected claims: `SELECT reason_codes, COUNT(*) FROM decision_log WHERE decided_at_utc >= ? GROUP BY 1`.

## Incident note template
```
Title:
Detected (IST) / by:            Paused (IST):            Resumed (IST):
What happened (one line):
Claims affected (count, ₹), rule_version:
Customers wrongly declined (count) and what we did for them:
Cause: rule change | data change | behaviour change, with evidence:
Fix and new rule_version:
Test: sweep rerun + unit test added (name):
What we change so it does not repeat:
Owner:                 Second on call:
```
