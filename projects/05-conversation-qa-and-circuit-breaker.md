# Project 5: Conversation Quality Auditor, Write-Back Check and Circuit Breaker

> Synthetic data, same structure as production. No real customer conversations. Do not send real transcripts to any external service without approval.

## 1. At a glance

| | |
| --- | --- |
| **Problem** | Automated conversations (a support bot) settle some questions and mishandle others. Quality varies, handovers can lose context, some answers touch on medicines, and when the bot misbehaves nobody notices until customers complain. |
| **What I build** | A quality auditor that scores bot and agent conversations against a rubric, a safety check for medicine answers, a tested write-back of outcomes into ticket fields, and a circuit breaker that pauses the automation when quality drifts, with a runbook and named second on call. |
| **Headline measure** | Share of conversations resolved, handed over with context, and policy-compliant; agreement between the auditor and human labels; and minutes from drift to pause. |
| **Fix owner** | CX analytics (monitoring, runbook), Product and the bot vendor (fixes). |
| **JD lines** | Conversation systems; same standard for every automated conversation; full context on handover; outcome on every session; define the payload, test it, read back, alert on failure; first on call; review AI assistant output. |

## 2. Problem at the company

- A bot answers order questions, but some answers are wrong or incomplete.
- When the bot hands over to an agent, the agent sometimes sees no context and the customer repeats everything.
- Some sessions end with no recorded outcome, so resolution cannot be measured.
- Medicine-related answers carry a safety risk; a wrong answer can affect a pet's care.
- Automated flows write fields into tickets; a silent failure leaves records blank.

## 3. Goals and non-goals

**Goals**

- Score every conversation, bot or human, on the same rubric.
- Flag unsafe medicine answers and missing "consult a vet" guidance.
- Prove that outcome fields are written correctly and alert when they are not.
- Pause the automation quickly when quality drifts.

**Non-goals**

- No change to the bot's content or logic from this project; it measures and flags. Content changes go through the owner after approval.
- No medical advice generation. The auditor only checks for presence of safe-guidance language and escalation.

## 4. Data (synthetic)

| Table | Purpose | Key fields |
| --- | --- | --- |
| `transcripts` | Conversations | conversation_id, handler (bot or agent), started_at_utc, ended_at_utc, messages_json, outcome |
| `tickets` | Ticket records the bot writes to | ticket_id, conversation_id, status, bot_outcome, handover_flag, handover_summary, updated_at_utc |
| `audit_scores` | Auditor output | conversation_id, rubric_version, resolved, handover_with_context, policy_followed, medicine_safety, sentiment, notes, scored_at_utc |
| `human_labels` | Sample labelled by a person | conversation_id, same fields as the rubric |
| `webhook_log` | Write-back attempts | event_id, conversation_id, payload_hash, sent_at_utc, http_status, stored_ok |
| `breaker_state` | Circuit breaker | system, state (closed, open, half_open), changed_at_utc, reason |

**Planted patterns:** some bot sessions end with no outcome; some handovers have an empty summary; a few medicine answers lack the vet-consult line; one flow has a high handover rate; a webhook returns an error for a short window.

## 5. Quality rubric (versioned)

| Field | Values | Definition |
| --- | --- | --- |
| `resolved` | yes, no, unclear | the customer's question was answered without needing an agent or the agent finished it |
| `handover_with_context` | yes, no, not_applicable | on handover the agent received intent, order id and what the bot already tried |
| `policy_followed` | yes, no | refund and escalation policy followed as written |
| `medicine_safety` | ok, missing_disclaimer, unsafe, not_applicable | medicine topics include a "consult a vet" line and no dosing advice |
| `sentiment` | positive, neutral, negative | customer's tone at the end |
| `outcome_recorded` | yes, no | the session has an outcome in the ticket |

Keep the rubric in a file with a version number. A change creates a new version and the old scores are never rewritten.

## 6. The auditor

**Two modes**

1. **Rules-only mode (offline, free).** Deterministic checks that need no model:
   - `outcome_recorded`: outcome field present
   - `handover_with_context`: handover summary non-empty and contains an order id or intent
   - `medicine_safety`: if the text mentions a medicine keyword list, require the disclaimer phrase
2. **Model-assisted mode.** A language model (called through an official SDK, with the API key from an environment variable) scores the judgement fields (`resolved`, `policy_followed`, `sentiment`) and returns JSON that matches a fixed schema. Use structured output so every field is always present, with a short evidence quote.

**Prompt design**

- Provide the rubric, the policy text, and the transcript with personal data redacted.
- Ask for evidence: the quote that supports each judgement.
- Ask for `unclear` when evidence is missing, instead of guessing.
- Keep the prompt and schema in the repo, versioned, and reviewed like code.

**Redaction before scoring:** replace phone numbers, emails, addresses and names with tokens. Never log raw transcript text.

**Cost and speed:** score a daily sample for routine monitoring (for example 5%), and 100% of conversations for flows under review. Batch requests and cache the static rubric and policy text. Set a daily spend cap and alert when it is reached.

## 7. Checking the auditor itself

An automated score is only useful if it agrees with a person.

1. A reviewer labels about 100 conversations using the same rubric.
2. Compare auditor and human labels per field: agreement percentage and a chance-corrected measure such as Cohen's kappa.
3. Read the disagreements and adjust the prompt or rubric; record the new version.
4. Re-check agreement on a fresh sample whenever the prompt or model changes.
5. Treat the auditor's output like a colleague's work: review it, do not accept it by default.

## 8. Outputs

- **Daily quality table:** conversations scored, resolved rate, handover-with-context rate, policy-followed rate, medicine-safety issues, negative sentiment share, by flow and by handler.
- **Flows with most handovers:** ranked list with sample conversation ids for the owner to read.
- **Safety list:** every conversation with `missing_disclaimer` or `unsafe`, sent to the owner the same day.
- **Weekly summary:** trend, top three problems, and the recommendation.

## 9. Write-back check (webhook payload to ticket fields)

**Define the payload**

```json
{
  "event_id": "uuid",
  "conversation_id": "string",
  "ticket_id": "string",
  "bot_outcome": "resolved | handed_over | abandoned",
  "handover_flag": true,
  "handover_summary": "intent, order id, what the bot already tried",
  "schema_version": "1"
}
```

**Test it before go-live**

1. Send the payload to a test ticket created for the purpose.
2. Read the ticket back through the API and compare each field to what was sent.
3. Repeat with edge cases: empty summary, very long text, special characters, duplicate `event_id`, wrong ticket id.
4. Confirm a duplicate event does not create a second update (idempotency by `event_id`).

**In production**

- Log every attempt in `webhook_log` with the HTTP status and a read-back result (`stored_ok`).
- Alert when the failure rate or the read-back mismatch rate exceeds a limit.
- Retry with backoff; after the last retry, park the event and alert.
- Secrets (API keys, tokens) come from environment variables, never from code or logs.

## 10. Circuit breaker

**What it watches** (rolling 60-minute and 24-hour windows)

| Metric | Example limit |
| --- | --- |
| Share of sessions with no outcome | above 5% |
| Handover-without-context share | above 15% |
| Medicine safety failures | any `unsafe`, or `missing_disclaimer` above 2% |
| Webhook write-back failure rate | above 2% |
| Negative sentiment share | 10 points above the 4-week baseline |
| Wrong-answer reports from agents | above a set count per hour |

**States**

```
closed  --(limit exceeded for N minutes)-->  open  --(owner review, manual or timed)-->  half_open
half_open --(test slice healthy)--> closed        half_open --(still bad)--> open
```

- **Open** means the automation is paused or reduced to a safe fallback (for example route everything to agents, or serve a fixed message), and the owner is alerted immediately.
- **Half-open** means a small slice of traffic runs again to confirm recovery.
- Every state change is logged in `breaker_state` with the metric and value that caused it.

**Rule for unsafe medicine answers:** the breaker opens on the first confirmed `unsafe` case for that flow, because the cost of one error is high.

## 11. Runbook (one per system)

| Heading | Content |
| --- | --- |
| Owner and second on call | named people and how to reach them |
| How to pause | the exact setting or command, and how to confirm it took effect |
| Who to tell | product owner, vendor contact, floor manager, with a message template |
| First checks | recent prompt or content change, vendor incident page, data feed, webhook errors |
| Find the cause | compare the last good and first bad hour; sample the conversations |
| Fix and restart | approved fix, test on a small slice, half-open, then closed |
| Write it down | incident note: time, impact, cause, fix, what changes so it does not repeat |

**Incident note template**

```
Incident: <system> paused at <time IST>
Detected by: <breaker metric / person>
Impact: <conversations affected, customers affected>
Cause: <what changed or failed>
Fix: <what was done, who approved>
Recovery: <time resumed, checks passed>
Follow-up: <changes to monitoring, content, tests>
```

## 12. Reviewing AI-assisted code

Where an AI assistant helps write the auditor or the webhook code, apply the same review as for a colleague:

- Read every line and be able to explain it.
- Run the tests and a negative test (bad input, empty fields, timeouts).
- Check for secrets in code or logs, and for personal data sent outside the approved path.
- Check that retries cannot create duplicates.
- Note in the pull request which parts were AI-assisted and how they were verified.

## 13. Privacy and safety

- Redact personal data before any text leaves the system.
- Pharmacy and pet-health details are sensitive; limit who can see scored transcripts.
- Keep scored text for a defined retention period, then keep only scores.
- The auditor never generates medical advice; it only checks for safe language and escalation.

## 14. Testing

1. Unit tests for each rules-only check with positive and negative fixtures.
2. Schema test: the model response always matches the output schema; a malformed response is rejected and retried.
3. Medicine keyword test: answers with and without the disclaimer line.
4. Webhook tests listed in section 9, with read-back.
5. Breaker simulation: replay a recorded bad hour and confirm it opens within the target time and that half-open recovers.
6. Agreement test against the human-labelled sample.

## 15. Requirement note (ready to copy)

**Problem.** Automated conversations vary in quality, some handovers lose context, medicine answers carry safety risk, and failures are found by customers.

**Proposed change.** Score conversations on a fixed rubric, check medicine safety, test and monitor outcome write-back, and add a circuit breaker with a runbook.

**Rules.** Rubric version 1; breaker limits as listed; unsafe medicine answer opens the breaker.

**Data.** Transcripts (redacted), tickets, webhook logs. Outputs: `audit_scores`, `webhook_log`, `breaker_state`.

**Test plan.** Unit tests, human-labelled agreement check, webhook read-back tests, breaker replay simulation.

**Success measure.** Auditor agreement with human labels above __%; failures detected and the system paused within __ minutes; outcome recorded on at least __% of sessions.

**Owner and sign-off.** Manager signs off; Product and the vendor agree the pause mechanism.

## 16. Deliverables checklist

- [ ] Synthetic transcripts with planted problems
- [ ] Rubric file with version
- [ ] Rules-only auditor and model-assisted auditor with redaction
- [ ] Human-labelled sample and agreement report
- [ ] Daily quality table and flow handover ranking
- [ ] Webhook payload spec, tests and read-back check
- [ ] Circuit breaker with state log
- [ ] Runbook, incident template, named second on call
- [ ] Requirement note and README

## 17. Results (fill in after running)

| Measure | Value |
| --- | --- |
| Conversations scored | |
| Resolved, handover-with-context, policy-followed rates | |
| Medicine answers missing the disclaimer | |
| Auditor agreement with humans (percentage, kappa) | |
| Webhook write-back success and read-back match | |
| Time from injected fault to breaker open | |

## 18. Risks and trade-offs

- A sensitive breaker protects customers but may pause a healthy bot; the half-open test and a review step reduce wasted downtime.
- Scoring every conversation is thorough but costs more; sampling is cheaper but may miss a rare failure.
- An automated judge can be wrong or biased; human sampling and versioned prompts keep it honest.

## 19. What I would do next

- Use the weekly flow ranking to propose content fixes to the owner as requirement notes.
- Feed `handover_summary` quality back to the bot owner so context improves at the source.
- Extend the breaker to the refund engine from Project 1 and the outreach lists from Project 2.

## 20. Interview talking points

- "I check the auditor against human labels, and I review AI-written code like a colleague's."
- "Every automated flow writes an outcome, and I read back what was stored."
- "One unsafe medicine answer is enough to pause the flow."
