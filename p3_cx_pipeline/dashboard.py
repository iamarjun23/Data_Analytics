"""Dashboard built only from the marts (mart_daily) and the pipeline's own health tables.
Run (from p3_cx_pipeline/): py dashboard.py  ->  dashboard.md
"""
import pathlib

from pipeline.metrics import METRICS
from pipeline.run import connect

OUT = pathlib.Path(__file__).resolve().parent / "dashboard.md"


def main():
    con = connect()
    days = [r[0] for r in con.execute("SELECT DISTINCT day FROM mart_daily ORDER BY day DESC LIMIT 14")][::-1]
    vals = {(r["day"], r["metric"]): r for r in con.execute("SELECT * FROM mart_daily")}

    def cell(d, m):
        r = vals.get((d, m))
        if r is None or r["value"] is None:
            return ""
        v = f"{r['value']:.1%}" if METRICS[m]["kind"] == "rate" else f"{r['value']:,.0f}"
        return v + ("*" if r["provisional"] else "")

    last = con.execute("SELECT run_id, MAX(finished_at_utc) t FROM run_log").fetchone()
    lines = ["# CX daily dashboard", "",
             f"Built from `mart_daily` (definitions in [metrics.md](metrics.md)). Last run: `{last['run_id']}` "
             f"finished {last['t']} UTC. Dates are IST. `*` = provisional (outcomes still arriving).", "",
             "| Day (IST) | " + " | ".join(METRICS) + " |", "|---|" + "---|" * len(METRICS)]
    lines += [f"| {d} | " + " | ".join(cell(d, m) for m in METRICS) + " |" for d in days]

    lines += ["", "## Pipeline health (latest run)", "", "| Table | Status | In | New | Changed | Rejected | Watermark |",
              "|---|---|---|---|---|---|---|"]
    for r in con.execute("SELECT * FROM run_log WHERE run_id = ?", (last["run_id"],)):
        lines.append(f"| {r['table_name']} | {r['status']} | {r['rows_in']} | {r['rows_new']} | {r['rows_changed']} | "
                     f"{r['rows_rejected']} | {r['watermark_after']} |")
    bad = con.execute("SELECT * FROM dq_results WHERE checked_at_utc = (SELECT MAX(checked_at_utc) FROM dq_results) "
                      "AND status IN ('fail','warn')").fetchall()
    lines += ["", "## Data-quality checks not passing", ""]
    lines += [f"- **{r['status']}** {r['table_name']} `{r['check_name']}` = {r['value']} (threshold {r['threshold']}) "
              f"{r['detail'] or ''}" for r in bad] or ["- none"]
    rec = con.execute("SELECT * FROM recon_results WHERE run_id = ?", (last["run_id"],)).fetchall()
    lines += ["", "## Reconciliation to source (last 14 days)", ""]
    lines += [f"- {r['day']} {r['table_name']} {r['team']}: source {r['src_count']} vs staging {r['stg_count']} "
              f"(diff {r['diff']}) cause: {r['cause'] or 'to investigate'}" for r in rec] or ["- all days match"]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
