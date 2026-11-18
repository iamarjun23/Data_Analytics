# Project 2: Order-Delay Early-Warning Outreach

> Synthetic data, same structure as production. No real customer data or internal numbers.

## 1. At a glance

| | |
| --- | --- |
| **Problem** | Orders miss their estimated delivery date (EDD), go out of stock after the order, or ship to a wrong address. Customers find out first and write in. |
| **What I build** | A risk flag on every open order, a daily work list that tells CX who to message and with what, a holdout group, consent and quiet-hour rules on every message, and a weekly impact report. |
| **Headline measure** | Contacts per 100 orders for treated versus holdout among the same flagged orders, in contacts and rupees. |
| **Fix owner** | Supply chain (courier and EDD buffer), category (stock), CX (messaging). |
| **JD lines** | Outreach systems and daily work lists; measuring what each list produced; rules that apply to every message; spikes; systems you do not control. |

## 2. Problem at the company

- Many orders are delayed and the EDD is late, so "where is my order" (WISMO) tickets pile up.
- The delay is not spread evenly: a few lanes and couriers cause most of it.
- Some items are out of stock after the customer has paid, and a separate team handles those orders.
- Some addresses are wrong, and an OTP that never arrives blocks delivery.
- CX reacts after the customer writes in. The aim is to reach the customer first, with the right message.

## 3. Goals and non-goals

**Goals**

- Flag orders likely to miss EDD early enough to act.
- Give CX a daily list that says what to do per order.
- Measure tickets avoided with a holdout, not by assumption.
- Apply consent and quiet-hour rules to every message, and log exclusions.

**Non-goals**

- No courier switching or rerouting decisions; supply chain owns that. The project gives them evidence.
- No promotional content in these messages. They are order updates.

## 4. Data (synthetic)

| Table | Purpose | Key fields |
| --- | --- | --- |
| `orders` | Order and promise | order_id, account_id, order_ts_utc, lane, courier, promised_date, delivered_ts_utc, status, address_ok, stock_status |
| `shipment_events` | Courier scans | order_id, event_ts_utc, status_code, location |
| `tickets` | Contacts | ticket_id, order_id, created_at_utc, reason, channel |
| `consent` | Per channel permission | customer_id, channel, consent_status, dnd_flag |
| `risk_flags` | Output of the flag job | order_id, run_date, risk_score, reasons, list_date |
| `outreach_list` | Daily work list | list_date, order_id, action, template_id, is_holdout |
| `outreach_log` | What was actually sent | order_id, sent_at_utc, channel, template_id, status |
| `exclusions` | Why someone was not contacted | order_id, run_date, reason (no_consent, dnd, quiet_hours, cap, holdout) |

**Planted patterns:** three lanes on one courier are late far more often; some orders have no scan for two or more days; Dog food goes out of stock after order more than other categories; about 7% of addresses are wrong; about 4% of tickets have no order id.

## 5. The risk flag

For every open order, once or twice a day:

| Signal | Example rule |
| --- | --- |
| Lane prior | lane and courier late rate over the last 28 days above 25% |
| Scan gap | no courier scan for more than 36 hours |
| Packing age | still not shipped N hours after order, N depends on category |
| Stock | order marked out of stock or backordered |
| Address | pincode and city mismatch, missing landmark, invalid phone |
| Promise | promised date is within the next 2 days and order is not out for delivery |

```sql
-- lane prior: rolling 28 day late rate per lane and courier (DuckDB)
SELECT lane, courier,
       AVG(CASE WHEN delivered_ts_utc::date > promised_date THEN 1.0 ELSE 0 END) AS late_rate_28d
FROM orders
WHERE status = 'delivered'
  AND delivered_ts_utc >= current_date - INTERVAL 28 DAY
GROUP BY lane, courier;
```

`risk_score` is a transparent sum of signals scaled 0 to 1; `reasons` lists which signals fired so the agent can explain it.

## 6. Actions per flag

| Flag | Action | Message type |
| --- | --- | --- |
| Lane or scan gap, order still moving | proactive delay notice with a new EDD | order update |
| Out of stock after order | offer wait, substitute or refund | order update |
| Address looks wrong | confirm address before dispatch | order update |
| High risk and high order value | agent callback | call |

The new EDD is the lane's median actual transit plus a buffer, shown with the assumption in the template notes.

## 7. Daily work list

Columns the team sees: order id, customer first name only, lane, reasons, recommended action, template id, preferred channel, call-by time. Nothing else personal. The list is generated at a fixed time each morning (IST), with a run record.

## 8. Compliance filter (built into the list job)

Applied before a row reaches the list, every exclusion logged in `exclusions`.

1. **Consent:** only channels the customer has consented to.
2. **DND:** respected for anything outside strictly transactional order updates; when in doubt, treat as excluded.
3. **Quiet hours:** no calls or messages outside 09:00 to 21:00 IST (assumption to confirm with compliance).
4. **Frequency cap:** at most one proactive message per order per 24 hours, and at most __ per customer per week.
5. **Sensitive data:** no medicine names or pet health details in message templates.

## 9. Holdout design

- **Among flagged orders only.** Randomly assign 10% of flagged orders (`hash(order_id) % 10 = 0`) to no proactive message. Comparing flagged treated against all unflagged orders would be wrong, because flagged orders are different.
- Holdout orders still get normal service when the customer writes in.
- Pre-register the outcome before the test: WISMO tickets per 100 flagged orders within 7 days of the promised date.
- Secondary: all contacts per 100, refunds and credits, CSAT, cancellations, next-order rate within 30 days.

## 10. Measurement and weekly report

| Item | Treated | Holdout | Difference |
| --- | --- | --- | --- |
| Flagged orders | | | |
| WISMO tickets per 100 | | | |
| All contacts per 100 | | | |
| Refunds and credits (₹) | | | |
| Cancellation rate | | | |
| CSAT | | | |

Then the cost side: messages sent, agent minutes on callbacks, and the rupee value of tickets avoided (tickets avoided times cost per contact from the assumptions table). Also report **false alarms** (flagged but delivered on time) and **misses** (late but not flagged), so the list is judged on both sides.

## 11. Data risks to handle

- **Late edits:** delivered dates and statuses are edited after the fact; re-read the last 3 days on every run.
- **Courier status changes:** unknown status codes go to an UNMAPPED bucket and raise an alert.
- **Timezones:** store UTC, report and schedule in IST.
- **Tickets with no order id:** report the share and the join coverage next to every result.

## 12. Spike test

Sale days and festival weeks can double orders in hours. Test the flag and list job with 2x and 3x synthetic volume: record run time, list freshness at the cut-off, and whether any step fails or times out. Document the point where the job needs a bigger batch or a faster query.

## 13. Testing and rollout

1. Unit tests on each signal with fixture orders.
2. Run on test orders and test numbers; read back the stored `outreach_list` and `outreach_log` rows.
3. Shadow week: produce lists, send nothing, compare flags with what happened.
4. Pilot on one lane and one template with the holdout live.
5. Widen lane by lane after the weekly review.

## 14. Monitoring and runbook

| Signal | Check | Action |
| --- | --- | --- |
| List not ready by cut-off | scheduled check | alert owner, fall back to yesterday's list |
| Flag rate jumps | daily against 4-week average | check for a data issue before acting |
| Messages failing to send | per run | alert and retry; log failure |
| Opt-out or complaint rate | weekly | pause the template, review the content |

## 15. Requirement note (ready to copy)

**Problem.** Customers contact us about late and at-risk orders before we contact them.

**Proposed change.** Flag at-risk open orders daily and give CX a work list with a recommended action; message early with a corrected EDD.

**Rules.** Flag when lane late rate, scan gap, packing age, stock or address signals fire. Respect consent, DND, quiet hours and the frequency cap.

**Data.** orders, shipment_events, tickets, consent. Outputs: `risk_flags`, `outreach_list`, `outreach_log`, `exclusions`.

**Test plan.** Unit tests, test orders and numbers with read-back, one shadow week, one-lane pilot with a 10% holdout.

**Success measure.** WISMO tickets per 100 flagged orders lower by at least __% in treated versus holdout, with no rise in cancellations or fall in CSAT.

**Risks.** Wrong EDD in a message; mitigation: use the lane's measured transit plus a buffer and review false alarms weekly.

**Owner and sign-off.** Manager signs off before the note goes to Product and supply chain.

## 16. Deliverables checklist

- [ ] Synthetic orders, scans, tickets generator with planted patterns
- [ ] Lane analysis query and table
- [ ] Risk flag job with reasons
- [ ] Daily list generator with compliance filter and exclusion log
- [ ] Holdout assignment and weekly impact report
- [ ] Spike test script and result note
- [ ] Runbook
- [ ] Requirement note and README

## 17. Results (fill in after running)

| Measure | Value |
| --- | --- |
| Share of late orders carried by the worst three lanes | |
| WISMO per 100 flagged orders: treated vs holdout | |
| Tickets avoided per week and rupee value | |
| False alarm rate and miss rate | |
| Run time at 1x, 2x, 3x volume | |

## 18. Risks and trade-offs

- Early messages can create contacts from customers who would not have noticed. The holdout shows the net effect.
- A cautious EDD protects trust but may lower conversion; track cancellations.
- Rerouting to a faster courier costs money; the lane analysis is what supply chain uses to decide.

## 19. What I would do next

- Add stock-out prediction with category and inventory teams.
- Feed address-quality checks into checkout so the error does not happen.
- Combine with the circuit breaker in Project 5 so a bad template can be paused fast.

## 20. Interview talking points

- "My holdout is among flagged orders, because comparing against unflagged orders would hide selection bias."
- "I report false alarms and misses every week, not just tickets avoided."
- "Consent and quiet hours are filters in the job itself, with every exclusion logged."
