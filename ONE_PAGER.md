# CX Analytics — five projects, one pager

_Synthetic data throughout; structure matches production, no real customer data. Numbers below are what the built code actually produced on that data — rerun any report.py to reproduce._

**1. Refund and claim decisions** — Agents decided every refund claim by hand, inconsistently. Auto-decision engine with a ₹1,500 limit clears 55% of claims, holdout-measured net **₹11,547/week saved**, 2.5% wrong-approval rate on audit. Trade-off: raising the limit to ₹2,000 auto-clears 46 more claims but adds ₹11,355 in wrong approvals over the window — Finance owns that number, not me.

**2. Order-delay early warning** — Customers only found out an order was late by messaging in. Proactive flagging on scan gaps and lane risk cuts WISMO contacts **22.8 → 14.4 per 100 flagged orders** (holdout-measured, -8.4, CI -14.2 to -2.7), ~73 contacts/week avoided. Trade-off: 34.4% of flagged orders would have arrived on time anyway — false alarms cost outreach effort on orders that didn't need it.

**3. CX data pipeline and metrics** — Two dashboards gave different numbers for the same metric; vendor fields changed without notice. Built a 3-day-lookback incremental pipeline with schema contracts, an UNMAPPED bucket, daily source reconciliation, and a tool that decomposes a dashboard gap by cause (timezone, late edits, duplicates, denominator). Trade-off: failing a run on a renamed field protects the report but delays it — needs a fast-fix path, not just an alert.

**4. One customer, many identities** — Refill outreach was at risk of contacting the same person twice, or skipping consent/DND rules. Identity resolution found **24 duplicate-account groups** across 3 weeks (freeing that many call slots) with **zero** false merges; outreach run lifted 14-day reorder rate **+6.0 pts**, net ₹3,606 after agent cost. Trade-off: holdout is small — the lift's confidence interval doesn't yet exclude zero, so I'd call this promising, not proven.

**5. Conversation quality and circuit breaker** — Bot/agent conversations needed auditing for policy breaks and unsafe answers before they reached scale. Daily auditor tracks resolution, handover-with-context, and negative-ending rate (11–13%/day); a rules-based breaker opens on a confirmed unsafe case, runs a test slice through owner review, and closes when healthy. Trade-off: rules mode only catches known patterns — broadening coverage means a better rubric, not more rules.

**One sentence each, in case there's no time for the rest:** I built and tested five systems that mirror the CX-analyst job — decisions, outreach, data ownership, identity, and conversation safety — each measured against a holdout, not just a before/after.
