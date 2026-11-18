# Runbook: order-delay early-warning list

**Owner:** CX analytics. **Second on call:** _name_ (from the team rota). **Business owner:** CX lead.
**Schedule:** 08:00 IST (list due by the 08:30 cut-off) and an evening top-up at 19:30 IST.
**Where to look:** `job_runs` (one row per run), `risk_flags`, `outreach_list`, `exclusions`, `outreach_log`, and the Monitoring and Compliance sections of [report.md](report.md).

All commands run from the repo root. Every rule knob is at the top of [job.py](job.py).

## Alerts and what to do

| Signal | Check | What it means | Do this |
| --- | --- | --- | --- |
| List not ready by cut-off | `job_runs.ready_by_cutoff = 0`; the job logs an error | The run failed or was too slow. | Alert the owner. CX works yesterday's list (`outreach_list` for the previous `list_date`) and skips orders delivered since. Rerun once fixed; the rerun adds only rows not already decided. |
| Flag rate jumps | Morning flagged / open > 1.5× the 4-week average (report, Monitoring) | Usually a **data** problem, not a real delay wave. A courier feed outage looks like a scan gap on every order. | Check the latest scan time per courier before acting. If a feed is down, pause sends (below) until it recovers. A pure volume spike does not move the share (see [spike_result.md](spike_result.md)). |
| UNMAPPED courier status codes | `job_runs.unmapped > 0`; warning logged each run | A courier started sending a new status code. | Map it in `job.KNOWN` with the courier team. Until then it still counts as a scan, so it does not create false gaps. |
| Messages failing to send | Failures after one retry, per run (`outreach_log.status`) | The messaging provider is down or a template was rejected. | Alert the messaging owner. Failures stay in `outreach_log`, and the frequency cap counts list rows, not deliveries, so nobody gets messaged twice. |
| Opt-out or complaint rate rises | Weekly | The message is unwelcome or wrong. | Pause that template (below), review the content with CX and compliance, relaunch on one lane. |
| Compliance read-back not 0 | report.md, Compliance section | A rule was bypassed (quiet hours, consent, DND, holdout, caps). | **Stop sends now.** Find the run in `job_runs`, fix the cause, add a test for it, rerun the tests. |
| False alarm rate climbs | Weekly report | The threshold is too low for current lanes, or a lane recovered. | Review the lane table. Raise `FLAG_AT` or `LANE_LATE` in a new commit and rerun `simulate.py` to see the miss rate before shipping. |

## How to pause

| To stop | Do this |
| --- | --- |
| One action (e.g. delay notices) | Remove its entry from `TEMPLATE_OF` in `job.py`. Flags are still written, so measurement continues. |
| All messages, keep the list for agents | Remove the message templates and keep `callback`. |
| Everything | Disable the scheduled run. CX goes back to inbound tickets only. |

Post in the CX ops channel within 30 minutes of pausing, using the template below.

## Who to tell

- **Supply chain / courier team:** lane and feed problems, new status codes.
- **Messaging owner:** send failures.
- **Compliance:** any non-zero compliance read-back, any template change.
- **CX lead:** any pause, and the weekly review.

## Finding the cause

1. **Data change?** New courier codes, a feed gap (latest scan per courier), or a sudden jump in one signal in `risk_flags.reasons`.
2. **Rule change?** `git log -p p2_order_delay_early_warning/job.py` since the last good run.
3. **Real change?** A lane genuinely slowing down shows in the lane table and in delivered-late counts a few days later.

Useful queries:

```sql
-- signal mix for the latest run
SELECT reasons, COUNT(*) FROM risk_flags WHERE run_id = (SELECT MAX(run_id) FROM job_runs) GROUP BY 1 ORDER BY 2 DESC;
-- exclusions by reason for the latest run
SELECT reason, COUNT(*) FROM exclusions WHERE run_id = (SELECT MAX(run_id) FROM job_runs) GROUP BY 1;
```

## Rollout plan

1. Unit tests (`py p2_order_delay_early_warning/test_job.py`) pass on every push.
2. Test orders and test numbers, with a read-back of stored rows.
3. Shadow week: lists produced, nothing sent; compare flags with outcomes.
4. One-lane pilot (BLR-DEL, `T_DELAY` only) with the holdout live.
5. Widen lane by lane after each weekly review.

## Incident note template

```
Title:
Detected (IST) / by:            Paused (IST):            Resumed (IST):
What happened (one line):
Runs / list dates affected, orders and customers affected:
Customers wrongly contacted (count, rule bypassed) and what we did for them:
Cause: data change | rule change | courier behaviour, with evidence:
Fix (commit) and test added:
What we change so it does not repeat:
Owner:                 Second on call:
```
