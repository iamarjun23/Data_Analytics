# Requirement note: order-delay early-warning outreach

_Synthetic numbers from a 3-week simulated run (29 Sep to 31 Oct 2026). Replace them with numbers from a shadow week on real data before sign-off._

**Problem.** Customers find out an order is late, out of stock or stuck on a bad address before we tell them, and then they write in. In the synthetic data, 65% of late deliveries sit on three CourierX lanes (BLR-NE, BLR-KOL, BLR-DEL) that carry only a quarter of delivered orders. Each "where is my order" (WISMO) contact costs about ₹55 of agent time, and late orders also drive goodwill credits and cancellations.

**Proposed change.** Flag at-risk open orders twice a day (08:00 and 19:30 IST). Give CX a work list that says what to send, to whom and by when. Message early with a corrected delivery date (EDD), offer a choice on out-of-stock items, and ask for address confirmation. Keep a 10% holdout so we can measure what the list really changed.

**Rules.**

| Rule | Value (placeholder, edit in `job.py`) |
| --- | --- |
| Flag threshold | risk score ≥ 0.40 (lane alone is not enough) |
| Lane signal | lane + courier late rate > 25% over the last 28 days |
| Scan gap | no courier scan for more than 36 hours |
| Packing age | older than the category's packing SLA (12 h pharmacy, 24 h food, 36 h accessories) |
| Stock and address signals | out of stock after order; address flagged as not deliverable |
| Near promise | promised date within 2 days and not out for delivery |
| New EDD in the message | lane's 28-day median transit + 1 day |
| Agent callback instead of a message | risk ≥ 0.70 and order value ≥ ₹2,200 |
| Quiet hours | contact only between 09:00 and 21:00 IST |
| Frequency caps | one contact per order per 24 h; two proactive contacts per customer per 7 days |
| Consent and DND | message only on channels with consent; DND blocks calls (order updates by message are transactional; confirm with compliance) |
| Content | order updates only: no promotions, product, medicine or pet-health detail |

**Data.** Reads `orders`, `shipment_events`, `tickets` and `consent`. Writes `risk_flags`, `outreach_list`, `outreach_log`, `exclusions` (every row that was dropped, with the reason) and `job_runs` (one row per run with counts, run time and whether the list was ready by the 08:30 cut-off). The list carries first name, order id and channel only.

**Test plan.**
1. Unit tests for each signal, action, compliance rule, the holdout and every template (`test_job.py`, run on every push).
2. Test orders and test numbers end to end, with a read-back of the stored list and log.
3. One shadow week: lists produced, nothing sent; compare flags with what actually happened.
4. One-lane pilot (BLR-DEL, delay notices only) with the 10% holdout live.
5. Widen lane by lane after each weekly review.

**Success measure, treated vs holdout.**

| Measure | Target | Synthetic result |
| --- | --- | --- |
| WISMO tickets per 100 flagged orders | at least 20% lower | 14.4 vs 22.8 (37% lower; 95% CI of the gap −14.2 to −2.7) |
| Cancellations / RTO | no rise | 14.9% vs 19.2% |
| CSAT | no fall | 3.49 vs 2.74 |
| Compliance read-back | 0 on every check | 0 on all six |
| List ready by the cut-off | every run | every run (≈0.1 s at 1x, 1.3 s at 10x volume) |

**Risks.**
- **A wrong EDD in a message.** Mitigation: use the lane's measured transit plus a buffer, and review false alarms weekly.
- **False alarms.** At a 0.40 threshold, 34% of flagged orders arrived on time, and a heads-up on an on-time order creates a few contacts of its own. Raising the threshold lowers false alarms but raises the miss rate (8.6% today); the weekly report shows both.
- **A courier feed outage** looks like a scan gap on every order. The flag-rate monitor catches it; pause sends while a feed is down.
- **Contacting someone who opted out.** The compliance filter runs inside the list job, not after it, and the read-back stops sends if any check is non-zero.

**Rollback.** Remove a template from `TEMPLATE_OF` to stop that action, or stop the scheduled run. CX goes back to working inbound tickets only. See [the runbook](../../p2_order_delay_early_warning/RUNBOOK.md).

**Owner and sign-off.** The manager signs off before this goes to Product and supply chain. Compliance confirms quiet hours, DND handling and template wording. The CX lead owns the list and the weekly review.

| | Name | Date |
|---|---|---|
| Analyst (owner) | | |
| Manager | | |
| Compliance | | |
| Supply chain | | |
