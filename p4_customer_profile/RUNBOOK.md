# Runbook: customer profile and refill outreach

**Owner:** CX analytics. **Second on call:** _name_ (from the team rota). **Compliance contact:** _name_.
**Schedule:** profile rebuilt daily; refill list run weekly (both manual in this repo).
**Where to look:** [coverage_report.md](coverage_report.md) (published with every build), `coverage_history`, `cluster_review`, `shared_phones`, `match_candidates`, `outreach_runs`, `exclusions`, and [outreach_report.md](outreach_report.md).

All commands run from the repo root.

## Alerts and what to do

| Signal | Check | What it means | Do this |
| --- | --- | --- | --- |
| `HOLD: <source> coverage X% -> Y%` | Every build; more than 3 points drop on any source (`COVERAGE_DROP_HOLD`) | A source changed format (phone layout, a new field) or a feed is partial. **The build is rolled back; the previous profile stays live.** | Compare a few unmatched records in `unmatched_records` with older ones. Fix the normaliser or wait for the feed. If the drop is real and explained (e.g. a new channel with no account), rerun with `--force` and write the reason in the build note. |
| Cluster size spike | `cluster_review` has new rows (> 5 accounts or > 5 phones) | A shared number (clinic desk, courier, family phone) is gluing strangers together. | Check `shared_phones`. Never accept an oversized cluster without review. A confirmed shared number stays out of linking. |
| Tier D queue grows fast | `match_candidates` count vs last build | Usually a data change (pet or first names defaulted). | Do not bulk-accept. Tier D is review only; in the synthetic data 181 of 182 candidates were different people. |
| `WARN: exclusion share X% -> Y%` | Every list run; more than 10 points jump (`exclusion_jump_pts`) | The consent or DND feed is late, empty or reversed. | **Do not send.** Check the consent feed's last update and row count. Rerun the list after the feed is fixed. |
| `Read-back failed ... nothing sent` | Every list run | The stored list does not match what was built, or a blocked customer slipped in. | The run aborts on its own. Find the run in `outreach_runs`, fix, add a test, rerun. |
| Opt-out rate above 1% | Weekly, in outreach_report.md | The script or the timing is unwelcome. | Pause the script (below), review it with compliance and the pharmacy owner. |

## How to pause

| To stop | Do this |
| --- | --- |
| A new profile from going live | Do nothing: a held build already keeps the previous profile. |
| Refill calls | Stop running `refill_outreach.py`; send the agents no list. |
| One reason code (e.g. `reorder_due`) | Filter it out in `refill_outreach.py` in a reviewed commit, then rerun. |

## Who to tell

- **Compliance:** any consent or DND problem, any script change.
- **Pharmacy owner:** any issue with refill reminders.
- **Clinic operations:** duplicate booking groups (agents confirm; nothing is cancelled automatically).
- **CX lead:** any pause.

## Privacy checks (each build)

- `customer_360` holds hashed ids and flags only, never phones, medicine names or consult detail.
- Only the list job reads `contact_lookup` (raw phones). See the access grants in [README.md](README.md#privacy-and-access).
- Every list run records who ran it, when and for what purpose in `outreach_runs` (`--purpose` is required).

## Useful queries

```sql
-- coverage trend per source
SELECT source, built_at, matched_pct FROM coverage_history ORDER BY source, built_at DESC;
-- why records did not match
SELECT source, reason, COUNT(*) FROM unmatched_records GROUP BY 1, 2 ORDER BY 3 DESC;
-- exclusions in the latest list run
SELECT reason_code, COUNT(*) FROM exclusions WHERE run_id = (SELECT MAX(run_id) FROM outreach_runs) GROUP BY 1;
```

## Incident note template

```
Title:
Detected (IST) / by:            Paused (IST):            Resumed (IST):
What happened (one line):
Build or list run affected (built_at / run_id), customers affected:
Wrong merges or wrong contacts (count) and what we did for them:
Cause: source change | matching rule | consent feed, with evidence:
Fix (commit) and test added:
What we change so it does not repeat:
Owner:                 Second on call:
```
