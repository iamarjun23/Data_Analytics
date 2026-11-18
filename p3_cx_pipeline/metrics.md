# Metric definitions

Written first, then built. Each one is coded once in [`pipeline/metrics.py`](pipeline/metrics.py), and both the dashboard (`mart_daily`) and the disagreement tool compute from that code. If a definition changes here, change it there in the same pull request.

Common rules:

- Timestamps are stored in UTC. Each metric is converted **once** to an IST date on its anchor column.
- The population is the denominator for a rate, and the set being counted for a count.
- **Provisional** means a day can still change because outcomes arrive late. The dashboard marks it with `*`.
- The "Other dashboards often use" column is what [`pipeline/disagree.py`](pipeline/disagree.py) tests for its "denominator" cause.

| Metric | Kind | Population | Date anchor (IST) | Rule (numerator) | Provisional | Other dashboards often use |
|---|---|---|---|---|---|---|
| `tickets_created` | count | All tickets | Ticket created date | every ticket | no | Leave out `bot_handoff` tickets |
| `resolved_tickets` | count | Tickets with a resolved time | Resolved date | status group is `resolved` **now** (reopened tickets drop out) | no | Any ticket with a resolved time, even if reopened |
| `late_orders` | count | Delivered orders | Promised date | delivered date later than promised date | 4 days | Count RTO orders as late |
| `late_order_rate` | rate | Delivered orders | Promised date | delivered date later than promised date | 4 days | Add RTO orders to the denominator |
| `handover_rate` | rate | Bot conversations | Conversation start date | passed to an agent before closing | 2 days | Only conversations that already have an outcome |
| `call_answer_rate` | rate | All inbound calls | Call start date | disposition maps to `answered` | no | Leave out calls under 5 seconds |

`late_order_rate` and `handover_rate` match the shared definitions in the [root README](../README.md#shared-metric-definitions-used-by-every-project).

## Detail and caveats

### Handover rate
- **Population:** bot conversations.
- **Date anchor:** conversation start date, IST.
- **Rule:** the conversation was passed to an agent before closing (`outcome` maps to `handed_over`).
- **Denominator:** bot conversations started that day, **including those with no outcome yet**.
- **Caveat:** outcomes arrive up to 2 days late, so the last 2 days are provisional and read low. A dashboard that leaves out conversations with no outcome reads higher on recent days. That is the most common reason two handover numbers differ.

### Late orders and late order rate
- **Population:** orders whose current status maps to `delivered`. Returned-to-origin (RTO), cancelled and in-transit orders are left out.
- **Date anchor:** promised date, IST. This is the shared definition, so the rate for a day means "of the orders promised that day".
- **Rule:** the delivered date (IST) is later than the promised date (IST). It compares dates, not times, so an order delivered at 23:00 on its promised day counts as on time.
- **Caveat:** couriers correct delivery times up to 2.5 days after the fact. Those edits only become visible late, which is why the loader re-reads 3 days. A promised date stays provisional for 4 days because late deliveries are still arriving.

### Resolved tickets
- **Population:** tickets with a resolved time.
- **Date anchor:** resolved date, IST (the latest resolution).
- **Rule:** the status group is `resolved` (`solved` or `closed`) as of the last load.
- **Caveat:** about 8% of tickets reopen. The vendor keeps the old resolved time on a reopened ticket, so counting by resolved time alone inflates the number. Past days can go down when a ticket reopens. `reconcile.py` lists those changes as "as reported vs as of today".

### Tickets created, call answer rate
- These are anchored on creation or start time, so they are never edited later.
- The one exception is call dispositions: about 3% of `NA` calls are corrected to `ANS` the next day.

## Change process

1. Edit this file and `pipeline/metrics.py` in the same pull request.
2. Run `py test_pipeline.py`. One test checks that the dashboard value equals the definition.
3. Run `py -m pipeline.run --backfill FROM TO` if the change has to restate past days. It saves `bak_stg_*` backups first.
