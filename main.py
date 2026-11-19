"""Run all five CX analytics projects against the shared synthetic data, one at a time,
printing what problem each one solves and where to read the result.

Run:  py main.py          # all five, in order
      py main.py 3        # just project 3
      py main.py 1 4      # just those two
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PY = sys.executable


def run(desc, cmd, cwd):
    print(f"\n  -> {desc}")
    print(f"     $ {' '.join(map(str, cmd))}")
    subprocess.run(cmd, cwd=cwd, check=True)


def banner(n, title, problem):
    print(f"\n{'=' * 70}\nP{n}: {title}\n{'=' * 70}\nProblem: {problem}")


def p1():
    banner(1, "Refund and claim decisions",
           "Agents decide every refund claim by hand. Every rule cuts both ways: too loose "
           "loses money, too strict annoys genuine customers.")
    run("Run the decision engine, sweep the approval limit, measure against the holdout, write the report",
        [PY, "p1_refund_decisions/report.py"], ROOT)
    print("  Read: p1_refund_decisions/out/report.md  (net saving vs holdout, wrong-approval rate, threshold sweep)")


def p2():
    banner(2, "Order-delay early warning",
           "Customers only find out an order is late by messaging in. By then it's too late to head it off.")
    run("Self-check: signal rules, holdout split, consent/DND filter, one run on test data, read back what was stored",
        [PY, "p2_order_delay_early_warning/test_job.py"], ROOT)
    run("Run the real job on the project's data and write the weekly impact report",
        [PY, "p2_order_delay_early_warning/report.py"], ROOT)
    print("  Read: p2_order_delay_early_warning/report.md  (WISMO tickets avoided, treated vs holdout)")


def p3():
    banner(3, "CX data pipeline and metrics",
           "Vendor data changes without notice and gets edited after creation. Two dashboards "
           "disagree and nobody knows why.")
    cwd = ROOT / "p3_cx_pipeline"
    run("Incremental load with a 3-day look-back (catches records edited after the fact)",
        [PY, "-m", "pipeline.run", "--lookback-days", "3"], cwd)
    run("Data-quality checks: freshness, duplicates, unmapped values, row-count drop",
        [PY, "-m", "pipeline.checks"], cwd)
    run("Reconcile staging counts against the source, per day",
        [PY, "-m", "pipeline.reconcile"], cwd)
    print("  Read: p3_cx_pipeline/dashboard.md  (the metrics dashboard built from these runs)")


def p4():
    banner(4, "One customer, many identities",
           "The same person has several accounts and phone numbers. Outreach can double-contact "
           "people or break consent/DND rules.")
    run("Resolve duplicate identities, report overlap",
        [PY, "p4_customer_profile/build_profile.py"], ROOT)
    run("Run consent-safe refill outreach with a holdout (--purpose is required: every run logs why)",
        [PY, "p4_customer_profile/refill_outreach.py", "--purpose", "main.py demo run"], ROOT)
    print("  Read: p4_customer_profile/coverage_report.md and outreach_report.md")


def p5():
    banner(5, "Conversation quality and circuit breaker",
           "Bot/agent conversations can go wrong at scale (policy breaks, unsafe answers) before a human notices.")
    run("Audit transcripts against the rubric, run the circuit breaker, write the quality report",
        [PY, "p5_conversation_qa/report.py"], ROOT)
    print("  Read: p5_conversation_qa/report.md")


PROJECTS = {1: p1, 2: p2, 3: p3, 4: p4, 5: p5}

if __name__ == "__main__":
    chosen = [int(a) for a in sys.argv[1:]] or list(PROJECTS)
    for n in chosen:
        PROJECTS[n]()
    print("\nDone. Each project wrote its report into its own folder - open those .md files next.")
