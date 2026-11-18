# Requirement note: conversation quality auditor, write-back check and circuit breaker

**Problem.** Automated conversations vary in quality. Some handovers lose context, medicine answers carry a safety risk, some sessions end with no recorded outcome, and failures are found by customers.

**Proposed change.** Score every conversation (bot or agent) on a fixed, versioned rubric. Check medicine answers for the vet-consult line and for dosing advice. Test and monitor the outcome write-back into ticket fields, with read-back. Add a circuit breaker with a runbook and a named second on call. This project measures and flags only. Bot content changes go through Product and the vendor after approval.

**Rules.**
- Rubric version 2 is current (version 1 is kept, and its scores are never rewritten).
- Breaker limits:

| Metric | Window | Limit | Minimum sample |
| --- | --- | --- | --- |
| Sessions with no outcome | 60 min | > 5% | 30 |
| Handovers without context | 60 min | > 15% | 10 |
| Medicine answers missing the vet line | 24 h | > 2% | 30 |
| Webhook write-backs not stored | 60 min | > 2% | 20 |
| Negative sentiment | 24 h | baseline + 10 points | 100 |
| Wrong-answer reports from agents | 60 min | set with the floor manager | needs a feed |

- Every limit also needs at least 4 events and must hold for 5 minutes. **An unsafe medicine answer opens the breaker on the first case.**

**Data.** Inputs: transcripts (redacted before any text leaves the system), tickets, webhook logs. Outputs: `audit_scores`, `webhook_log`, `parked_events`, `breaker_state`.

**Test plan.** Unit tests for each rule, with passing and failing examples. A schema test for model output. Webhook edge cases with read-back (empty, long and special-character text, duplicate `event_id`, wrong ticket, outage). A breaker replay of a recorded bad hour. An agreement check against 100 human-labelled conversations, repeated whenever the rubric, prompt or model changes.

**Success measure.**

| Measure | Target |
| --- | --- |
| Auditor agreement with human labels | above 90% per field, kappa above 0.8 |
| Failures detected and the system paused | within 30 minutes |
| Outcome recorded | on at least 99% of sessions |
| Write-back read-back match | 100% of stored events |

On synthetic data the result was 99-100% agreement, a pause within 21 minutes, 98.9% of sessions with an outcome recorded, and 0 read-back mismatches.

**Owner and sign-off.** The manager signs off. Product and the bot vendor agree the pause mechanism (the vendor console's "route all to agents" setting) and who may flip it. CX analytics owns monitoring and the runbook. Product and the vendor own fixes.
