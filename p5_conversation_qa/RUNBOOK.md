# Runbook: support bot (system `support_bot`)

The circuit breaker (`breaker.py`) writes every state change to `breaker_state` with the metric and value that caused it. This runbook is what the person on call does when it opens.

> Names below are placeholders. Replace them before go-live and keep this table current.

| Heading | Content |
| --- | --- |
| **Owner (first on call)** | `<CX analytics analyst>`: Slack `#cx-bot-oncall`, phone `<on-call number>` |
| **Second on call** | `<named colleague>`: same channel, phone `<number>`. Takes over if the first on call has not acknowledged within 10 minutes |
| **Product owner** | `<product manager, conversation systems>` |
| **Vendor contact** | `<bot vendor support desk>`, priority line `<number>`, incident page `<url>` |
| **Floor manager** | `<support floor manager>`: warns agents that more chats will come to them |

## 1. How to pause

- **Pause:** in the bot vendor console, set flow routing to **"Route all to agents"** (or the safe fallback: a fixed message, *"Our team will reply shortly"*, and route to agents). For a medicine problem, pause **only `pharmacy_refill`** first, then decide on the rest.
- **Confirm it took effect:** open a test chat on the website and check that it lands with an agent within 1 minute. Then check that `transcripts` shows no new `handler = 'bot'` sessions for 5 minutes.
- Record the pause in `breaker_state` (state `open`, reason "manual pause by `<name>`") if it was not opened by the breaker.

## 2. Who to tell (within 15 minutes)

Tell the product owner, the vendor contact and the floor manager. Template:

```
[Bot paused] support_bot - <flow or all> paused at <HH:MM IST>.
Why: <metric> = <value> (limit <limit>) since <HH:MM IST>. Sample ids: <C..., C..., C...>.
Effect: chats go to agents; expect +<n> chats/hour on the floor.
Next update: <HH:MM IST>. On call: <name>, second: <name>.
```

## 3. First checks (in this order)

1. **A recent prompt or content change?** Check the vendor's release log for the last 24 hours. The planted bad hour (5 Nov, 13:30 IST) was a release.
2. **Vendor incident page:** is the vendor reporting an outage?
3. **Data feed:** are order and tracking lookups returning data? Empty lookups make empty handover summaries.
4. **Webhook errors:** `SELECT http_status, COUNT(*) FROM webhook_log WHERE sent_at_utc > <last hour> GROUP BY 1;` and `SELECT COUNT(*) FROM parked_events;`

## 4. Find the cause

- Compare the **last good hour** and the **first bad hour**: run `report.py` and read the daily table, then the same metrics by flow.
- Sample **10 conversations** from the first bad hour (ids from `audit_scores.notes`, and the safety list for medicine). Read them **only in the access-controlled viewer**, not by exporting the text.
- Write the cause in one sentence before fixing anything.

## 5. Fix and restart

1. The fix is made by the **owner** (Product or the vendor) and approved by the product owner. CX analytics does not edit bot content.
2. **Half-open:** send a **10% slice** of traffic to the bot for 30 minutes and watch the same metrics.
3. If the slice is healthy, **close** the breaker (full traffic). If not, open it again and go back to step 4.
4. **Parked write-backs:** replay `parked_events` once the ticket API is healthy. Replays are idempotent by `event_id`, so this cannot double-update a ticket. Then check that the read-back match is 100% for the replayed events.

## 6. Write it down (same day)

```
Incident: support_bot paused at <time IST>
Detected by: <breaker metric and value / person>
Impact: <conversations affected>, <customers affected>, <minutes paused>
Cause: <what changed or failed>
Fix: <what was done, who approved>
Recovery: <time resumed, checks passed: half-open slice metrics>
Follow-up: <changes to monitoring, content, tests; owner and date>
```

### Worked example (from the replay)

```
Incident: support_bot paused at 13:50 IST, Thu 5 Nov 2026
Detected by: breaker, no_outcome_share = 6/71 (8.5%, limit 5%) for 5 min
Impact: ~85 conversations in the bad hour; 35% ended with no outcome, half the handovers had an empty summary
Cause: prompt release at 13:30 IST dropped the outcome step and the vet-consult line
Fix: vendor rolled back the release, approved by <product owner>
Recovery: first half-open slice still bad (no_outcome 18%), reopened 14:50; second slice healthy, closed 15:50 IST
Follow-up: vendor releases go through a 10% slice first; add "outcome step present" to the release checklist
```
