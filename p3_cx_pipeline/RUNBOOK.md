# Runbook: CX data pipeline

**Owner:** CX analytics. **Second on call:** named in the team rota. Source fixes go through the vendor and Product.
**Schedule:** daily at 00:30 UTC (06:00 IST), plus manual runs (`workflow_dispatch`). On sale days, turn on the extra cron line in the workflow.
**Where to look:** the `alerts`, `run_log`, `dq_results` and `recon_results` tables, and `dashboard.md` (uploaded as a build artifact on every run).

All commands run from `p3_cx_pipeline/`.

| Alert / failure | What it means | Do this |
|---|---|---|
| `field missing or renamed: X` (run failed) | The vendor renamed or dropped a field. **Nothing was loaded for that source**, and the other sources did load. | 1. Check `alerts` for a `new field (not loaded): Y` from the same run, which is usually the new name. 2. Confirm with the vendor. 3. Update `contracts.json` (and the code if needed) in a pull request. 4. Rerun. The watermark did not move, so the next run picks up everything missed. Tell report readers that the source is a day behind. |
| `type changed: X is now T` (run failed) | Same as above, for a changed type (for example, a number now sent as text). | Same steps. Never cast silently. |
| `new field (not loaded): X` (warn) | The vendor added a field. Known fields loaded normally. | Add it to the contract only if a metric needs it (least data). |
| `UNMAPPED <field> values (add to map_x.csv): v xN` | A new status, channel, queue or disposition value. Rows loaded, with `UNMAPPED` in the mapped column. | Add the value to `maps/map_*.csv` in a pull request, merge, then rerun. Mappings reload every run. To restate past rows, run `py -m pipeline.run --backfill FROM TO`. |
| `check freshness_hours` fail | The newest record in a source is older than 26 hours. | Check the vendor status page and the API errors in `run_log.error`. Tell report readers that the source is stale. |
| `check duplicate_keys` fail | Should be impossible (primary key plus upsert). | Stop and investigate before the next run. Restore from `bak_stg_*` if a backfill caused it. |
| `check timezone_sanity` fail | Timestamps in the future or before launch: a source switched timezone or date format. | Compare a raw payload (`raw_*.payload`) with the contract `format`, then fix the format. |
| `check blank_rate:order_ref` warn | More than 8% of tickets have no order reference. | Check whether a channel stopped capturing it. Coverage is also tracked in `ticket_order_coverage`. |
| `check unmapped_share` warn | More than 1% of recent rows are `UNMAPPED`. | See the UNMAPPED row above. |
| `check row_count_vs_4wk_weekday` warn | Yesterday's rows are under 70% of the 4-week same-weekday average. | Check whether it's a holiday or a real drop. If neither, look at the vendor export and backfill. |
| `reconciliation DAY TEAM: source A vs staging B` | Staging differs from the vendor's own daily count. | Find the cause (rejected rows, a failed run, an outage window), then write it in `recon_results.cause`. Run `--backfill` for those days. |
| `fetch failed: RateLimited` | Six retries with backoff were not enough. | Rerun later, or lower the page rate. Nothing was half-loaded. |

## Backfill

```bash
py -m pipeline.run --backfill 2026-11-01 2026-11-05
py -m pipeline.checks
py -m pipeline.reconcile
```

A backfill re-reads every record updated in those IST days and upserts it. It writes `bak_stg_<source>` first. To undo, copy the backup over the table:

```bash
sqlite3 ../data/p3.db "DELETE FROM stg_orders; INSERT INTO stg_orders SELECT * FROM bak_stg_orders;"
```

## Two dashboards disagree

```bash
py -m pipeline.disagree --metric late_order_rate --from 2026-11-08 --to 2026-11-14 --b 0.21 --b-as-of 2026-11-15T00:30:00+00:00
```

The tool prints the causes ranked by how much of the gap each explains, with a fix for each one. Paste the table into the reply to your manager.
