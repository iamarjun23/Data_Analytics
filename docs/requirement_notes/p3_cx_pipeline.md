# Requirement note: CX data pipeline, metric definitions and reconciliation

_Synthetic numbers from 14 daily runs on a mock vendor API, ending 18 Nov 2026. Replace them with a one-week parallel run against the current reports before sign-off._

**Problem.** CX reports depend on vendor platforms we do not control (ticketing, orders, telephony, bot). Fields get renamed, new status values appear without notice, and records are edited days after they are created. Reports break silently, and two dashboards often show different numbers for the "same" metric because each one uses its own timezone, population or data snapshot.

**Proposed change.**
- A scheduled **incremental load** (daily at 06:00 IST) with a **3-day look-back**, so late edits are picked up.
- A **schema contract** per source. A renamed field or changed type fails that source's load *before* anything is written; other sources still load.
- **Mapping tables** (status, channel, queue, disposition) kept as CSV in Git, with a visible `UNMAPPED` bucket and an alert that names the value.
- **Data-quality checks** (freshness, duplicates, blank rates, UNMAPPED share, row-count drop, timezone sanity, ticket-to-order coverage) and a **run log**.
- **Daily reconciliation** of our counts against each vendor's own daily count.
- **Written metric definitions** (population, date anchor, rule, denominator), coded once and used by every dashboard.
- A **"why do two dashboards disagree?"** tool that breaks a gap down by cause.

**Rules.**

| Rule | Value |
| --- | --- |
| Look-back | 3 days before the last watermark |
| Watermark on failure | does not move; the next run re-reads everything missed |
| Upsert | on the source key; the newest `updated_at` wins, so re-runs never duplicate |
| Timestamps | stored in UTC, reported in IST |
| PII | phone and email hashed with a salted SHA-256 at staging; marts never see them |
| Freshness limit | newest record older than 26 h → fail |
| UNMAPPED share | above 1% of recent rows → warn |
| Row-count drop | under 70% of the 4-week same-weekday average → warn |
| Tickets with no order reference | above 8% → warn |

**Data.** Reads the ticketing, orders, telephony and bot APIs (read-only token in a real setup). Writes `raw_*` (append-only, as received), `stg_*` (typed, deduplicated, mapped), `mart_*` and `mart_daily`, plus `run_log`, `dq_results`, `recon_results` and `alerts`. A backfill writes a `bak_stg_*` copy first.

**Test plan.**
1. Unit tests for mappings, date parsing and timezones.
2. Fixtures for a renamed field, a type change, a new status value, a late edit and duplicates.
3. Idempotency (running twice gives identical tables) and backfill tests.
4. A test that the dashboard and the written definitions give the same numbers.
5. One week in parallel with the current reports, with every difference explained.

**Success measure.**

| Measure | Target | Synthetic result |
| --- | --- | --- |
| Source changes caught before the next report | 100% | 4 of 4 schema changes; 324 of 324 unknown values routed to UNMAPPED |
| Rows out of date against the vendor | 0 | 0 with the 3-day look-back (167 without it) |
| Daily reconciliation differences | 0 or explained | 0 over 14 days and 4 sources |
| Dashboard gap explained by cause | 100% | 100% for each of 3 test metrics |

**Risks.**
- **Failing on a rename delays one source by a day.** Mitigation: the alert names the likely new field, and the fix is a one-line contract change.
- **An edit older than the look-back is missed.** Mitigation: a dated backfill command, with a backup taken first.
- **Hashing slows some investigations** (you must hash the lookup value to join). Accepted for privacy.
- **Raw tables hold PII as received.** Only the pipeline role may read them.

**Rollback.** Restore a staging table from its `bak_stg_*` copy, or rerun from raw. See [the runbook](../../p3_cx_pipeline/RUNBOOK.md).

**Owner and sign-off.** The manager signs off. Product and each vendor are told about the contract and agree to announce schema changes. CX analytics owns the pipeline, the definitions and the runbook.

| | Name | Date |
|---|---|---|
| Analyst (owner) | | |
| Manager | | |
| Product | | |
