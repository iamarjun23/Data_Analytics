# Project 3: CX Data Pipeline, Metrics and Reconciliation

> Synthetic data, same structure as production. No real customer data, credentials or vendor field names.

## 1. At a glance

| | |
| --- | --- |
| **Problem** | CX tables come from vendor platforms (ticketing, telephony, messaging, bot, orders). Fields get renamed, new values appear, and records are edited days later. Reports break without warning, and two dashboards give different numbers. |
| **What I build** | A scheduled incremental pipeline with a 3-day look-back, schema contracts, status and channel mappings with a visible UNMAPPED bucket, data-quality checks, a run log, daily reconciliation to source, written metric definitions, and a tool that explains why two dashboards disagree. |
| **Headline measure** | Time to detect a source change (target: before the next report), and the share of dashboard differences explained by cause. |
| **Fix owner** | CX analytics (me), with vendor and Product for source fixes. |
| **JD lines** | Own the CX tables from source to report; systems you do not control; mappings with a visible bucket for unknown values; reconcile per day; look-back reconciliation; metrics defined in writing; least-privilege access and no secrets in code. |

## 2. Problem at the company

- Order and ticket data lives in separate platforms with different ids and date formats.
- Records are edited after creation: tickets reopen, delivered dates are corrected, statuses change.
- Vendor platforms rename fields or add status values without notice.
- Two dashboards can show different "resolved tickets" or "late orders" because of timezone, late edits, duplicates or a different denominator.

## 3. Goals and non-goals

**Goals**

- Load only what changed, but re-read recent days so late edits are caught.
- Detect schema changes and unknown values before they reach a report.
- Reconcile each table to its source every day.
- Define every metric in writing and build the dashboard from those definitions.

**Non-goals**

- No real-time streaming in v1; a scheduled batch is enough.
- No heavy orchestration tool; a scheduled job (GitHub Actions or cron) is enough to prove the pattern.

## 4. Architecture

```
mock vendor API / export  -->  raw (as received)  -->  staging (typed, deduped, mapped)  -->  marts (metrics)
                                   |                         |                                  |
                              run_log + schema check     quality checks               reconciliation + freshness
```

- **Raw:** the response as received, with `loaded_at_utc` and `run_id`. Never edited.
- **Staging:** typed columns, UTC timestamps, deduplicated by key, mapped statuses and channels.
- **Marts:** tables that dashboards read, one grain each (ticket, order, conversation, day).

Technology (synthetic version): Python 3, DuckDB or Postgres, a mock API served from fixture files, GitHub Actions for the schedule.

## 5. Source data (synthetic)

| Source | Grain | Key | Edited later? |
| --- | --- | --- | --- |
| Ticketing | ticket | ticket_id | yes: status, resolved time, tags |
| Orders (store platform) | order | order_id | yes: delivered time, status |
| Telephony | call | call_id | rarely; disposition may be corrected |
| Bot or messaging | conversation | conversation_id | outcome added after the chat |

A mock API serves pages of records ordered by `updated_at`, with a page size and a rate limit, so the pipeline handles pagination, errors and retries.

## 6. Incremental load with a look-back

```python
# pseudo-code, one table
watermark = last_successful_watermark(table)            # from run_log
since = watermark - timedelta(days=3)                   # look-back for edited records
for page in fetch_pages(updated_since=since):           # handles pagination, retry with backoff
    upsert(staging_table, page, key="ticket_id")        # insert new, update changed, never duplicate
write_run_log(run_id, table, rows_in, rows_new, rows_changed, rows_rejected, status)
```

- **Upsert on the key**, so reprocessing the same record never duplicates it.
- **Backfill mode:** the same job with a date range, used after an outage or a mapping change.
- **Idempotent:** running twice gives the same table.
- **Back up before overwrite:** a snapshot of the staging table is written before any backfill.

## 7. Schema contract and drift detection

Each source has a contract file:

```yaml
source: ticketing
table: tickets
fields:
  ticket_id:   {type: string,   required: true}
  created_at:  {type: datetime, required: true}
  status:      {type: string,   required: true}
  channel:     {type: string,   required: true}
  order_ref:   {type: string,   required: false}
```

Each run compares the payload to the contract:

| Change | Result |
| --- | --- |
| Field missing or renamed | **fail the run**, alert, do not load |
| New field appears | warn, log, load known fields only |
| Type changes | fail the run |
| New value in a mapped column | load, send to UNMAPPED, alert |

## 8. Mappings with a visible UNMAPPED bucket

Mapping tables live in the repo as CSV and are loaded as tables:

| Table | Maps | Example |
| --- | --- | --- |
| `map_status` | platform status to analytics status | `pending_customer` to `waiting` |
| `map_channel` | platform channel to channel group | `wa_inbound` to `whatsapp` |
| `map_disposition` | call disposition to outcome | `NA` to `no_answer` |
| `map_queue` | queue and agent group to team | |

Any value not in the table gets `UNMAPPED`, and a check reports the count and sample values. A mapping change goes through a pull request and runs the check afterwards.

## 9. Data-quality checks

| Check | Rule | Severity |
| --- | --- | --- |
| Freshness | newest `updated_at` older than expected interval | fail |
| Duplicates | duplicate keys in staging | fail |
| Blank rate | required field blank above threshold (for example `order_ref` above 8%) | warn |
| Unknown values | share of UNMAPPED above 1% | warn |
| Row-count drop | today's rows below 70% of the 4-week same-weekday average | warn |
| Timezone sanity | timestamps in the future or before the system launch date | fail |
| Referential coverage | share of tickets joined to an order, reported every run | info, tracked |

Results go to a `dq_results` table (run_id, check, value, threshold, status) and a short message to the owner when any check fails.

## 10. Timezones and late records

- Store all timestamps in UTC; convert once, in the mart, to IST for date anchors.
- A ticket created at 23:30 IST belongs to the IST date, not the UTC date; the metric definitions say which anchor is used.
- Late records (edited after the report) are reflected on the next run; the daily reconciliation shows the difference between "as reported on day D" and "as of today".

## 11. Run log and reconciliation

```sql
CREATE TABLE run_log (
  run_id TEXT, table_name TEXT, started_at_utc TIMESTAMP, finished_at_utc TIMESTAMP,
  rows_in INT, rows_new INT, rows_changed INT, rows_rejected INT,
  watermark_before TIMESTAMP, watermark_after TIMESTAMP, status TEXT, error TEXT
);
```

Daily reconciliation compares staging with the source's own count per day and per team:

```sql
SELECT d.day,
       s.src_count,
       d.stg_count,
       d.stg_count - s.src_count AS diff
FROM stg_daily d
JOIN source_daily s USING (day)
WHERE ABS(d.stg_count - s.src_count) > 0;
```

A non-zero difference opens an investigation with a short written cause.

## 12. Metric definitions (written, then built)

Each metric has population, date anchor, rule and denominator (full table in the root README). Example for one:

**Handover rate**

- Population: bot conversations
- Date anchor: conversation start date, IST
- Rule: conversation passed to an agent before closing
- Denominator: bot conversations started that day
- Known caveats: outcomes are added up to 2 days later, so the last 2 days are provisional

Dashboards read only from the mart built on these definitions.

## 13. "Why do two dashboards disagree?" tool

Input: two numbers for the same metric and date range. The tool recomputes the metric while changing one thing at a time and reports how much of the gap each cause explains:

| Cause | How it is tested |
| --- | --- |
| Timezone | recompute with UTC dates versus IST dates |
| Late edits | recompute as of report day versus as of today |
| Duplicates | recompute with and without deduplication |
| Denominator | recompute with the other dashboard's population |
| Filters | list filters that differ (channel, team, status) |

Output: a cause-ranked table with the gap in orders or tickets that each cause explains, plus a recommended fix to take to the manager.

## 14. Security and privacy

- Credentials in environment variables or the platform's secret store; `.env.example` in the repo, real `.env` never committed.
- Read-only credentials for sources; write access only to the pipeline schema.
- Collect only the columns a table needs; phone and email are hashed in marts unless a use case requires them.
- Logs never contain customer content; they carry ids and counts.

## 15. Schedule

```yaml
# .github/workflows/cx_pipeline.yml (outline)
on:
  schedule:
    - cron: "30 0 * * *"   # 06:00 IST daily
  workflow_dispatch:
jobs:
  run:
    steps:
      - checkout
      - run: python -m pipeline.run --lookback-days 3
      - run: python -m pipeline.checks
      - run: python -m pipeline.reconcile
```

Failures notify the owner and the named second on call. A second, faster schedule runs during sale days.

## 16. Testing

1. Unit tests for the mapping and timezone functions.
2. A fixture where a field is renamed: the run must fail before loading.
3. A fixture with a new status: the value must land in UNMAPPED and raise an alert.
4. A fixture with an edited record two days later: the staging row must update, not duplicate.
5. Idempotency test: run the same window twice and compare row counts.

## 17. Requirement note (ready to copy)

**Problem.** CX reports depend on vendor data that changes without notice and is edited after creation; two dashboards sometimes disagree.

**Proposed change.** A scheduled incremental pipeline with a 3-day look-back, schema contracts, mapping tables with an UNMAPPED bucket, quality checks, a run log and daily reconciliation, plus written metric definitions.

**Data.** Ticketing, orders, telephony and bot exports. Outputs: staging tables, marts, `run_log`, `dq_results`.

**Test plan.** Fixtures for schema drift, new values, late edits and duplicates; idempotency test; one week in parallel with the current reports.

**Success measure.** Any source change is detected before the next report; daily reconciliation differences are zero or explained; two dashboards built on the definitions agree.

**Owner and sign-off.** Manager signs off; Product and the vendor are informed of the contract.

## 18. Deliverables checklist

- [ ] Mock vendor API with pagination and a rate limit
- [ ] Incremental loader with look-back and upsert
- [ ] Schema contracts and drift check
- [ ] Mapping tables and UNMAPPED report
- [ ] Data-quality checks and `dq_results`
- [ ] Run log and reconciliation queries
- [ ] `metrics.md` with definitions and a dashboard built on it
- [ ] Dashboard disagreement tool
- [ ] Scheduled workflow, runbook and README

## 19. Results (fill in after running)

| Measure | Value |
| --- | --- |
| Injected schema changes caught before load | |
| Injected unknown values routed to UNMAPPED | |
| Edited records reconciled by the look-back | |
| Daily reconciliation differences | |
| Share of dashboard gap explained by cause | |

## 20. Risks and trade-offs

- A longer look-back catches more late edits but costs more run time and API calls.
- Failing a run on a renamed field protects reports but delays them; the alert and a fast fix path matter.
- Hashing identifiers protects privacy but makes some investigations slower.

## 21. What I would do next

- Add column-level lineage so a metric can be traced to its source field.
- Add anomaly detection on volumes for sale-day spikes.
- Move the schedule to the company's orchestration tool once the pattern is proven.

## 22. Interview talking points

- "I assume vendor data will change and edit itself, so the pipeline expects it: contract check, UNMAPPED bucket, look-back."
- "Every metric has population, date anchor, rule and denominator in writing."
- "When dashboards disagree I decompose the gap by cause instead of guessing."
