# Project 5: Conversation quality auditor, write-back check and circuit breaker

Spec: [../projects/05-conversation-qa-and-circuit-breaker.md](../projects/05-conversation-qa-and-circuit-breaker.md)

> **All data is synthetic** (10,500 conversations, 1-7 Nov 2026, fixed seed). No real transcripts. Model mode is written but has **not been run**: it needs approval because it sends redacted text to an external API.

## Problem

A support bot answers order, refund and pharmacy questions. Some answers are wrong or missing, some handovers lose context, some sessions end with no outcome, and medicine answers can hurt a pet. Today, customers are the ones who notice.

## Approach

| Piece | File | What it does |
| --- | --- | --- |
| Synthetic data | `gen_conversations.py` | Transcripts, tickets and 100 human labels, with planted faults: a bad prompt release (5 Nov 13:30 IST), 2 unsafe dosing answers, missing vet lines, vague or empty handover summaries, bot refund promises, and one flow (`delivery_delay`) with a 52% handover rate |
| Rubric (versioned) | `rubric_v1.json`, `rubric_v2.json` | Fields, allowed values, medicine keywords, disclaimer phrases, policy text. A new version adds new score rows and never rewrites old ones |
| Auditor | `auditor.py` | Redaction (email, phone, address, pincode, names → tokens), rules-only checks, and an optional model mode: Claude (`claude-opus-5-5`) scores resolved, policy and sentiment with an evidence quote, using structured JSON output, a cached rubric and policy, a daily spend cap (`AUDIT_DAILY_CAP_USD`), and one retry on a malformed answer |
| Prompt (versioned) | `prompt_v1.md` | Asks for evidence for each field, and "unclear" instead of a guess |
| Write-back | `webhook.py` | Payload spec v1, validation, send → read back → compare, retry at 0/1/2/4 min, then park and alert. Idempotent by `event_id`. Logs only a hash of the payload, never its text |
| Circuit breaker | `breaker.py` | Rolling 60-minute and 24-hour windows, closed → open → half_open → closed, state log in `breaker_state` |
| Report | `report.py` → `report.md` | Daily quality table, by flow and handler, handover ranking with sample ids, safety list, agreement (% and Cohen's kappa), write-back and breaker summary |
| Runbook | `RUNBOOK.md` | How to pause and confirm, who to tell, first checks, fix and restart, incident template, named second on call (placeholders) |
| Requirement note | `REQUIREMENT_NOTE.md` | One page, ready for sign-off |
| Tests | `test_p5.py` | 21 tests, standard library only |

## Run

From `cx-analytics-projects/`:

```bash
py p5_conversation_qa/gen_conversations.py
```

```bash
py p5_conversation_qa/webhook.py
```

```bash
py p5_conversation_qa/auditor.py --rubric 1
```

```bash
py p5_conversation_qa/auditor.py --rubric 2
```

```bash
py p5_conversation_qa/breaker.py --rubric 2
```

```bash
py p5_conversation_qa/report.py --rubric 2
```

```bash
py -m unittest p5_conversation_qa/test_p5.py
```

Model mode (after approval, with `pip install anthropic` and `ANTHROPIC_API_KEY` set; 5% daily sample, or 100% of one flow under review):

```bash
py p5_conversation_qa/auditor.py --rubric 2 --mode model --sample 0.05
```

```bash
py p5_conversation_qa/auditor.py --rubric 2 --mode model --flow refund_request
```

## Results (synthetic week, rubric v2, rules mode)

| Measure | Value |
| --- | --- |
| Conversations scored | 10,500 (100%, rules mode; v1 and v2 each) |
| Resolved / handover-with-context / policy-followed | 91.0% / 94.8% / 99.3% |
| Medicine answers missing the disclaimer | 13 of 1,949 medicine conversations (0.7%); **2 unsafe** dosing answers, both caught |
| Auditor agreement with humans | v1: 98-100% per field, kappa 0.96-1.00. v2: 99-100%, kappa 0.96-1.00 |
| Webhook write-back success and read-back match | 8,748 of 8,792 stored (99.5%); read-back match 100% of stored; 44 parked during the outage, all alerted |
| Time from injected fault to breaker open | bad prompt release **21 min**; webhook outage **7 min**; unsafe answer **1 min** |
| False pauses (breaker opened on healthy traffic) | 1 in the week (v2): 4 of 19 handovers lacked context in one hour |
| Highest-handover flow | `delivery_delay`, 51.6% of bot sessions (next highest is 22.5%) |

### What the agreement check changed (rubric v1 → v2)

Reading the v1 disagreements with the human labels led to four changes, all recorded in `rubric_v2.json`:

1. A different vet-consult wording ("check with your veterinarian") was flagged as missing.
2. The rule accepted vague handover summaries (`intent=unknown; tried=none`).
3. A subtle refund promise ("you'll get your money back") was missed.
4. Sarcasm ("Great, thanks for nothing") was read as positive.

Under v1, the different wording also opened the breaker 3 times on healthy traffic, through the 24-hour medicine metric. Under v2 it did not. The one remaining disagreement is `resolved`. When a session has no outcome, the rules say "unclear" and the human says "no". That is a judgement call, and model mode is meant to settle it.

**Read these agreement numbers with care.** The human labels and the planted faults come from the same generator, so the rules can reach near-perfect agreement here. With real transcripts, expect lower agreement on the judgement fields (resolved, policy, sentiment). That is the reason model mode exists, and the reason to re-check agreement on a fresh sample every time the prompt or model changes.

### Recommendation

1. **Product and the vendor:** fix the `delivery_delay` flow. It hands over 52% of sessions, against 10-22% for the others. Give the bot the courier's hub-delay reason so it can answer.
2. **Vendor:** send every release through a 10% slice first. The 5 Nov release dropped the outcome step and the vet line, and took 21 minutes and two half-open slices to clear.
3. **Pharmacy owner:** review the 2 unsafe answers today. Make the vet line a fixed template block, not prompt text.

## Trade-offs

- **Sensitivity vs downtime:** with the spec's example limits alone, the breaker paused healthy traffic 7 times in the week (about 18 handovers an hour means 3 bad summaries is 17%). A minimum of 4 events plus a minimum sample per metric cut that to 1, with no slower detection of the planted faults (21, 7 and 1 minutes before and after). The cost: a slow-burn fault with low volume needs longer to reach the minimum. The half-open slice limits wasted downtime.
- **Sampling vs coverage:** rules mode is free, so it scores 100% and feeds the breaker. Model mode costs money, so it scores a 5% daily sample, or 100% of a flow under review. A rare failure can slip past the sample, so medicine is always checked by rules.
- **Automated judge bias:** the human sample, versioned rubric and prompt, and an evidence quote for every judgement keep the auditor honest.

## Limits and shortcuts (marked `ponytail:` in code)

- Redaction is regex-based, written for the formats in this data. A real rollout adds a named-entity pass for names in free text.
- There is one breaker for the whole bot. Split it per flow if pausing pharmacy should not stop order tracking.
- The negative-sentiment baseline uses the first 48 hours, standing in for 4 weeks.
- Model calls are synchronous. Move to the Message Batches API (50% cheaper) once daily volume justifies it.
- In the replay, "owner review" is a fixed 30 minutes, and the half-open slice uses all traffic in the period, not a 10% slice.
- Wrong-answer reports from agents are not watched yet; that needs an agent report feed.
- `FakeTicketAPI` stands in for the helpdesk API. `send_with_readback()` is the part that stays.

## AI-assisted code

This code was written with an AI assistant (Claude). It was verified by running the full pipeline, 21 unit tests that include negative cases (malformed model output, invalid payloads, wrong ticket, outage, duplicate events), and a check that redaction leaves no names, phones, emails or addresses across all 37,127 messages. Model mode has not been executed. Review it line by line before first use.
