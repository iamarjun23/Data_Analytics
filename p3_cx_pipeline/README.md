# P3: CX data pipeline, metrics and reconciliation

_All data is **synthetic**, with no real customers, credentials or vendor field names._

| | |
| --- | --- |
| **Status** | Built |
| **Spec** | [../projects/03-cx-data-pipeline-and-metrics.md](../projects/03-cx-data-pipeline-and-metrics.md) |
| **Requirement note** | [../docs/requirement_notes/p3_cx_pipeline.md](../docs/requirement_notes/p3_cx_pipeline.md) |
| **Runbook** | [RUNBOOK.md](RUNBOOK.md) |
| **Outputs** | [dashboard.md](dashboard.md) · metric definitions in [metrics.md](metrics.md) |
| **Check** | `test_pipeline.py` (10 tests) in CI on every push; the full pipeline runs daily at 06:00 IST ([cx_pipeline.yml](../.github/workflows/cx_pipeline.yml)) |

**Problem.** CX tables come from vendor platforms I don't control. Fields get renamed, new status values appear, and records are edited days later. Reports break without warning, and two dashboards show different numbers.

**What this does.** A scheduled incremental load with a 3-day look-back, schema contracts, mapping tables with a visible `UNMAPPED` bucket, data-quality checks, a run log, daily reconciliation to source, written metric definitions, and a tool that explains why two dashboards disagree.

## Results (synthetic run, 14 daily runs ending 18 Nov 2026)

| Measure | Value |
|---|---|
| Injected schema changes caught before load | **4 / 4** (3 renames, 1 type change). Each failed run loaded 0 rows for that source, and the other sources still loaded. |
| Injected unknown values routed to UNMAPPED | **324 / 324** (new ticket status `escalated_l2`), with an alert naming the value and the CSV to fix |
| Edited records reconciled by the look-back | Rows out of date against the vendor after 14 days: **167** with no look-back, **0** with 3 days |
| Daily reconciliation differences | **0** day/team rows differ over 14 days and 4 sources |
| Share of dashboard gap explained by cause | **100%** for each of 3 metrics where the other dashboard used UTC dates, report-morning data and a different population. Each cause was identified separately. |

Reproduce these with `python test_pipeline.py` (10 tests, about 10 s) and the demo commands below.

## How it works

```
mock vendor API  -->  raw_* (as received)  -->  stg_* (typed, UTC, deduped, mapped)  -->  mart_* / mart_daily
                         |                           |                                       |
                   run_log + contract check     dq_results (checks.py)            recon_results + late changes (reconcile.py)
```

| File | What it does |
|---|---|
| [mock_api.py](mock_api.py) | Synthetic ticketing, orders, telephony and bot APIs. Pages by `updated_at` with a rate limit and three date formats. Records are edited later, some edits only show up days late, and there are drift switches. |
| [contracts.json](contracts.json) | One schema contract per source: types, required fields, mappings, PII flags, blank-rate thresholds |
| [maps/](maps/) | `map_status`, `map_channel`, `map_disposition`, `map_queue` as CSV. Changes go through a pull request and reload every run. |
| [pipeline/run.py](pipeline/run.py) | Watermark minus look-back, fetch with retry and backoff, contract check (fail before load), raw append, staging upsert on key, UNMAPPED alert, `run_log`, `--backfill` (backs up first), marts |
| [pipeline/checks.py](pipeline/checks.py) | Freshness, duplicates, blank rate, UNMAPPED share, row-count drop compared with the 4-week same weekday, timezone sanity, ticket-to-order coverage, all written to `dq_results` |
| [pipeline/reconcile.py](pipeline/reconcile.py) | Staging vs the vendor's own count per IST day and queue; each metric "as reported on day D" vs "as of today" |
| [metrics.md](metrics.md) + [pipeline/metrics.py](pipeline/metrics.py) | Written definitions, coded once. The dashboard and the disagreement tool both use them. |
| [pipeline/disagree.py](pipeline/disagree.py) | "Why do two dashboards disagree?" Recomputes with timezone, late edits, duplicates, denominator and filters changed, and ranks the causes by the share of the gap each explains |
| [dashboard.py](dashboard.py) → [dashboard.md](dashboard.md) | Daily metrics read only from `mart_daily`, plus pipeline health |
| [test_pipeline.py](test_pipeline.py) | Units (mappings, timezones), renamed field, type change, new status, late edit, idempotency, backfill, definitions = dashboard |
| [RUNBOOK.md](RUNBOOK.md) | What each alert means and what to do |
| [../.github/workflows/cx_pipeline.yml](../.github/workflows/cx_pipeline.yml) | Daily at 06:00 IST: tests, then run, checks, reconcile, and the dashboard as a build artifact |

## Run it

Python 3.12 or later, standard library only (SQLite). The database goes to `../data/p3.db` (gitignored), or to `P3_DB` if set. Run from `p3_cx_pipeline/`:

```bash
python test_pipeline.py
```

```bash
python -m pipeline.run --as-of 2026-11-18T00:30:00+00:00 --replay-days 14
```

```bash
python -m pipeline.checks --as-of 2026-11-18T00:30:00+00:00
```

```bash
python -m pipeline.reconcile --as-of 2026-11-18T00:30:00+00:00
```

```bash
python dashboard.py
```

```bash
python -m pipeline.disagree --metric resolved_tickets --from 2026-11-08 --to 2026-11-14 --b 799 --b-as-of 2026-11-15T00:30:00+00:00
```

To inject drift, set `MOCK_DRIFT` before a run, for example `rename:ticketing.channel>channel_name`, `new_value:ticketing.status=escalated_l2` or `type:telephony.duration_s`. Several can be joined with `;`.

## Design choices

- **Raw is append-only.** Every fetched payload is kept with `run_id` and `loaded_at_utc`. That lets the disagreement tool rebuild "what we knew on the report morning" without snapshots.
- **Upsert on key, newest `updated_at` wins.** Re-reading never duplicates, and an older version never overwrites a newer one. Running twice gives identical tables (tested).
- **A failed source does not move its watermark.** The next run re-reads everything since the last good load.
- **PII is hashed at staging** with a salted SHA-256 (`PII_SALT` from the secret store), so marts and dashboards never see phone numbers or email addresses. Raw keeps the payload as received, so only the pipeline role should be able to read raw. Logs and alerts carry ids and counts only.
- **No secrets in code.** See [../.env.example](../.env.example). A real source would use a read-only token from the secret store, and the pipeline would write only to its own schema.

## Trade-offs and limits

- **Look-back length.** A 3-day look-back re-reads about 4 days of updates each run (about 3,000 records here). That catches courier corrections that show up up to 2.5 days late. A late edit older than the look-back still needs a backfill.
- **Fail on rename.** This protects reports but delays one source by a day. The alert names the likely new field so the fix is quick.
- **Hashing.** It protects privacy but makes some investigations slower: to join with a support tool you have to hash the lookup value too.
- **Disagreement attribution.** The tool tries every combination of causes and picks the one closest to B. With five causes, some combination can land close to B by chance, so read the "value alone" column too.
- **Synthetic.** The mock API is in-process (no HTTP), and the rate limit is deterministic (every 7th call). SQLite stands in for Postgres or DuckDB.

## What I would do next

- Add column-level lineage, so any dashboard number can be traced back to the source field it came from.
- Add volume anomaly detection for sale days (the row-count check today compares against a 4-week same-weekday average, which a planned sale will trip).
- Move the schedule from GitHub Actions to the company's orchestration tool once the pattern is proven, and swap SQLite for the warehouse.
- Feed the reconciled tables to [P2](../p2_order_delay_early_warning/README.md) and [P5](../p5_conversation_qa/README.md), so their reports read the same numbers the dashboard shows.
