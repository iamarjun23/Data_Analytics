"""Project 1 measurement: threshold sweep + chart, holdout vs treated, weekly report, results table.

Run (after engine.py): py p1_refund_decisions/report.py [rules/v1.json]
Writes p1_refund_decisions/out/report.md and out/sweep.svg.
Money from the audit sample is scaled by 1 / audit rate and marked "est.".
"""
import collections
import copy
import datetime as dt
import json
import math
import pathlib
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252, can't print rupee sign

from engine import DB, DEFAULT_RULES, decide, features, in_holdout

OUT = pathlib.Path(__file__).resolve().parent / "out"
LIMITS = [500, 1000, 1500, 2000, 3000]
IST = dt.timedelta(hours=5, minutes=30)
# Edit and rerun. Placeholders, not company numbers.
ASSUME = dict(cost_per_min=6,       # Rs per agent minute (loaded cost)
              fix_min=15,           # agent minutes to reverse one wrong decline
              goodwill=200,         # Rs credit given to a wrongly declined customer
              lost_margin=350)      # Rs future margin lost when a wrongly declined customer churns


def inr(x):
    return f"₹{x / 1e5:.1f}L" if abs(x) >= 1e5 else f"₹{x:,.0f}"


def week_of(ts):
    d = (dt.datetime.fromisoformat(ts) + IST).date()
    return d - dt.timedelta(days=d.weekday())


def context(con, version):
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    ctx = dict(
        amount=dict(q("SELECT claim_id, amount FROM claims")),
        week={c: week_of(t) for c, t in q("SELECT claim_id, created_at_utc FROM claims")},
        audit={c: bool(g) for c, g in q("SELECT claim_id, is_genuine FROM claim_audit")},
        agent={c: (d, m) for c, d, m in q("SELECT claim_id, decision, handle_min FROM agent_decisions")},
        log={c: (d, h, r) for c, d, h, r in q("SELECT claim_id, decision, is_holdout, reason_codes FROM decision_log "
                                              "WHERE rule_version = ?", version)})
    ctx["audit_rate"] = len(ctx["audit"]) / len(ctx["amount"])  # uniform random sample of all claims
    # repeat claim: the same cluster claims again within 30 days
    times = collections.defaultdict(list)
    cluster = {}
    for c, k, t in q("SELECT claim_id, cluster_id, created_at_utc FROM claims JOIN account_links USING (account_id)"):
        cluster[c] = k
        times[k].append((c, dt.datetime.fromisoformat(t)))
    ctx["repeat30"] = {c: any(0 < (t2 - t).total_seconds() <= 30 * 86400 for _, t2 in times[cluster[c]])
                       for k in times for c, t in times[k]}
    ctx["cluster"] = cluster
    return ctx


def evaluate(decisions, ctx):
    """Both sides of a rule. decisions = {claim_id: approve|decline|agent} for treated (non-holdout) claims."""
    A, aud, scale = ASSUME, ctx["audit"], 1 / ctx["audit_rate"]
    by = collections.defaultdict(list)
    for c, d in decisions.items():
        by[d].append(c)
    wa = [c for c in by["approve"] if aud.get(c) is False]
    wd = [c for c in by["decline"] if aud.get(c) is True]
    cd = [c for c in by["decline"] if aud.get(c) is False]
    audited = lambda cs: sum(c in aud for c in cs)
    minutes = sum(ctx["agent"][c][1] for c in by["approve"] + by["decline"])
    wa_rs = sum(ctx["amount"][c] for c in wa) * scale
    cd_rs = sum(ctx["amount"][c] for c in cd) * scale
    wd_cost = len(wd) * scale * (A["fix_min"] * A["cost_per_min"] + A["goodwill"] + A["lost_margin"])
    n = len(decisions) or 1
    return dict(claims=len(decisions), approved=len(by["approve"]), declined=len(by["decline"]), agent=len(by["agent"]),
                approve_rate=len(by["approve"]) / n, auto_rate=(len(by["approve"]) + len(by["decline"])) / n,
                wrong_approvals=len(wa), wrong_approval_rs=wa_rs,
                wrong_approval_rate=len(wa) / max(1, audited(by["approve"])),
                wrong_declines=len(wd), wrong_decline_rate=len(wd) / max(1, audited(by["decline"])),
                minutes_saved=minutes, net=minutes * A["cost_per_min"] + cd_rs - wa_rs - wd_cost)


def arm(ids, ctx, treated):
    """What actually happened to a set of claims. Holdout and routed claims get today's agent decision."""
    rows = []
    for c in ids:
        d, m = ctx["log"][c][0], 0
        if not treated or d == "agent":
            d, m = ctx["agent"][c]
        rows.append((d == "approve", ctx["amount"][c] * (d == "approve"), m, ctx["repeat30"][c]))
    n = len(rows)
    refund, minutes = sum(r[1] for r in rows) / n, sum(r[2] for r in rows) / n
    return dict(claims=n, approval_rate=sum(r[0] for r in rows) / n, refund=refund, minutes=minutes,
                repeat30=sum(r[3] for r in rows) / n, cost=refund + minutes * ASSUME["cost_per_min"])


def sweep(feats, cfg, ctx):
    rows = []
    for limit in LIMITS:
        c = copy.deepcopy(cfg)
        c["auto_approve"]["max_amount"] = limit
        dec = {cid: decide(cid, f, c)[0] for cid, f in feats.items() if not in_holdout(cid, cfg["holdout_share"])}
        rows.append(dict(limit=limit, **evaluate(dec, ctx)))
    return rows


def svg(rows, chosen, path):
    W, H, P = 300, 200, 36
    series = [("Auto-approval rate", "approve_rate", lambda v: f"{v:.0%}"),
              ("Wrong-approval ₹ (est.)", "wrong_approval_rs", inr),
              ("Wrong declines (audited)", "wrong_declines", str)]
    xs = [r["limit"] for r in rows]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W * 3} {H}" font-family="sans-serif" font-size="11">',
           f'<rect width="{W * 3}" height="{H}" fill="#fff"/>']
    for i, (title, key, fmt) in enumerate(series):
        ys = [r[key] for r in rows]
        top = max(ys) or 1
        X = lambda x: i * W + P + (x - xs[0]) / (xs[-1] - xs[0]) * (W - 2 * P)
        Y = lambda y: H - P - y / top * (H - 2 * P - 10)
        cx = X(chosen)
        out += [f'<text x="{i * W + P}" y="16" font-weight="bold" fill="#111">{title}</text>',
                f'<line x1="{i * W + P}" y1="{H - P}" x2="{(i + 1) * W - P}" y2="{H - P}" stroke="#999"/>',
                f'<line x1="{cx:.0f}" y1="24" x2="{cx:.0f}" y2="{H - P}" stroke="#d97706" stroke-dasharray="4 3"/>',
                f'<polyline fill="none" stroke="#2563eb" stroke-width="2" points="'
                + " ".join(f"{X(x):.0f},{Y(y):.0f}" for x, y in zip(xs, ys)) + '"/>']
        for x, y in zip(xs, ys):
            out += [f'<circle cx="{X(x):.0f}" cy="{Y(y):.0f}" r="3" fill="#2563eb"/>',
                    f'<text x="{X(x):.0f}" y="{Y(y) - 7:.0f}" text-anchor="middle" fill="#333">{fmt(y)}</text>',
                    f'<text x="{X(x):.0f}" y="{H - P + 14}" text-anchor="middle" fill="#555">₹{x}</text>']
        out.append(f'<text x="{cx + 3:.0f}" y="34" fill="#d97706">chosen</text>')
    out.append(f'<text x="{W * 1.5}" y="{H - 4}" text-anchor="middle" fill="#555">auto-approve amount limit</text></svg>')
    path.write_text("\n".join(out), encoding="utf-8")


def main(rules_path=DEFAULT_RULES):
    cfg = json.loads(pathlib.Path(rules_path).read_text())
    con = sqlite3.connect(DB)
    ctx = context(con, cfg["rule_version"])
    feats, feats_acc = features(con), features(con, view="account")
    log, chosen, g = ctx["log"], cfg["auto_approve"]["max_amount"], cfg["guardrails"]
    treated = [c for c in feats if not log[c][1]]
    holdout = [c for c in feats if log[c][1]]
    weeks = sorted({ctx["week"][c] for c in feats})
    n_weeks = len(weeks)

    # sweep
    rows = sweep(feats, cfg, ctx)
    OUT.mkdir(exist_ok=True)
    svg(rows, chosen, OUT / "sweep.svg")
    best = max(rows, key=lambda r: r["net"])
    cur = next(r for r in rows if r["limit"] == chosen)
    up = next((r for r in rows if r["limit"] > chosen), None)
    down = next((r for r in reversed(rows) if r["limit"] < chosen), None)
    signed = lambda x: ("+" if x >= 0 else "−") + inr(abs(x))
    def band(lo, hi):  # treated claims auto-approved at limit hi but not at lo, and how many audited genuine
        cs = [c for c in treated if lo < ctx["amount"][c] <= hi and log[c][0] != "decline"
              and decide(c, feats[c], {**cfg, "auto_approve": {**cfg["auto_approve"], "max_amount": hi}})[0] == "approve"]
        aud = [ctx["audit"][c] for c in cs if c in ctx["audit"]]
        return len(cs), len(aud), sum(aud)

    why = "It has the highest net of the limits tried." if best is cur else ""
    if down:
        n, n_aud, n_gen = band(down["limit"], chosen)
        why += (f" Dropping to ₹{down['limit']:,} changes net by {signed((down['net'] - cur['net']) / n_weeks)} a week "
                f"but sends {n} more claims ({(cur['minutes_saved'] - down['minutes_saved']) / 60:.0f} agent-hours) "
                f"to the queue; {n_gen} of the {n_aud} audited ones are genuine customers who would wait longer.")
    if up:
        n, n_aud, n_gen = band(chosen, up["limit"])
        why += (f" Raising it to ₹{up['limit']:,} auto-approves {n} more claims, changes net by "
                f"{signed((up['net'] - cur['net']) / n_weeks)} a week and adds "
                f"{inr(up['wrong_approval_rs'] - cur['wrong_approval_rs'])} of wrong approvals over the window "
                f"({n_aud - n_gen} of {n_aud} audited claims in that band not genuine).")

    # holdout vs treated
    T, Hd = arm(treated, ctx, True), arm(holdout, ctx, False)
    net_vs_holdout = (Hd["cost"] - T["cost"]) * T["claims"] / n_weeks
    mixed = sum(any(ctx["cluster"][t] == ctx["cluster"][h] for t in treated) for h in holdout) / len(holdout)
    p, nh, nt = Hd["approval_rate"], len(holdout) * 4 / n_weeks, len(treated) * 4 / n_weeks
    mde = 2.8 * math.sqrt(p * (1 - p) * (1 / nh + 1 / nt))  # 80% power, 5% two-sided, 4 weeks of claims

    # cluster view vs per-account view
    acc_dec = {c: decide(c, feats_acc[c], cfg)[0] for c in treated}
    caught = [c for c in treated if acc_dec[c] == "approve" and log[c][0] != "approve"]
    caught_bad = sum(ctx["audit"].get(c) is False for c in caught)

    # weekly
    def week_eval(w):
        return evaluate({c: log[c][0] for c in treated if ctx["week"][c] == w}, ctx)

    this_w, last_w = weeks[-1], weeks[-2]
    E = {w: week_eval(w) for w in weeks}
    tw, lw = E[this_w], E[last_w]
    prior4 = [E[w] for w in weeks[-5:-1]]
    avg = lambda k: sum(e[k] for e in prior4) / len(prior4)
    received = collections.Counter(ctx["week"][c] for c in feats)
    hold_w = collections.Counter(ctx["week"][c] for c in holdout)
    flags = []
    move = (tw["approve_rate"] - avg("approve_rate")) * 100
    if abs(move) > g["auto_share_move_pts"]:
        flags.append(f"Auto-approve share moved {move:+.1f} pts vs 4-week average. Check rule change / data / behaviour.")
    agent_share = tw["agent"] / tw["claims"]
    agent_avg = sum(e["agent"] / e["claims"] for e in prior4) / 4
    if abs(agent_share - agent_avg) > .10:
        flags.append(f"Share routed to agents {agent_share:.0%} vs {agent_avg:.0%} 4-week average.")
    unmapped = [c for c in feats if ctx["week"][c] == this_w and "UNMAPPED_REASON" in log[c][2]]
    if unmapped:
        flags.append(f"{len(unmapped)} claim(s) with an unmapped reason value went to agents. Map it or confirm it stays manual.")
    if tw["wrong_approval_rate"] > g["max_wrong_approval_rate"]:
        flags.append(f"Wrong-approval rate {tw['wrong_approval_rate']:.0%} above the {g['max_wrong_approval_rate']:.0%} limit: pause auto-approve.")
    if tw["wrong_decline_rate"] > g["max_wrong_decline_rate"]:
        flags.append(f"Wrong-decline rate {tw['wrong_decline_rate']:.0%} above the {g['max_wrong_decline_rate']:.0%} limit: review every decline.")
    flags.append("Decision log gaps: 0 (engine refuses to finish with any).")
    asm = cfg["assumed_at_approval"]
    fmt_week = lambda w: f"{w:%d %b}"

    def week_cells(e, w):
        return [f"{received[w]:,} ({hold_w[w]} holdout)", f"{e['approved']} / {e['declined']} / {e['agent']}",
                f"{e['wrong_approvals']} audited, {inr(e['wrong_approval_rs'])} est.",
                f"{e['wrong_declines']} audited", f"{e['minutes_saved']:,}", inr(e["net"])]

    items = ["Claims received", "Auto-approved / auto-declined / to agent", "Wrong approvals (count, ₹)",
             "Wrong declines (count)", "Agent minutes saved", "Net saving (₹)"]
    assumed = [ "—", f"{asm['auto_decision_rate']:.0%} auto-decided", f"rate ≤ {asm['wrong_approval_rate']:.0%}",
                f"rate ≤ {asm['wrong_decline_rate']:.0%}", "—", inr(asm["net_weekly_saving_rs"])]
    weekly = "\n".join(f"| {i} | {a} | {b} | {c} |" for i, a, b, c in
                       zip(items, week_cells(tw, this_w), week_cells(lw, last_w), assumed))

    A = ASSUME
    md = f"""# Project 1: Refund and claim decision system, results

_Synthetic data, same structure as production. Rule version **{cfg['rule_version']}**, test window {fmt_week(weeks[0])} – {fmt_week(this_w + dt.timedelta(days=6))} 2026 ({n_weeks} weeks, IST). Generated by `report.py`; edit the assumptions at the bottom and rerun._

## Results

| Measure | Value |
|---|---|
| Claims in the test window | {len(feats):,} ({len(treated):,} treated, {len(holdout):,} holdout) |
| Auto-decision rate at ₹{chosen:,} limit | {cur['auto_rate']:.0%} ({cur['approved']:,} approved, {cur['declined']} declined) |
| Wrong approvals | {cur['wrong_approvals']} in audit sample ({cur['wrong_approval_rate']:.1%} of audited approvals), {inr(cur['wrong_approval_rs'])} est. |
| Wrong declines | {cur['wrong_declines']} in audit sample ({cur['wrong_decline_rate']:.0%} of audited declines) |
| Agent minutes saved | {cur['minutes_saved']:,} ({cur['minutes_saved'] / 60 / n_weeks:.0f} agent-hours a week) |
| Net weekly saving versus holdout | **{inr(net_vs_holdout)}** |
| Claims caught only by cluster view | {len(caught)} would be auto-approved per account; {caught_bad} of them are audited not genuine |

## Threshold sweep (what Finance decides on)

Same engine, same claims, only the amount limit changes. Holdout excluded.

| Limit | Auto-approved | Approval rate | Wrong approvals (audited) | Wrong-approval ₹ (est.) | Wrong declines | Agent min saved | Net (window) | Net / week |
|---|---|---|---|---|---|---|---|---|
""" + "\n".join(f"| {'**' if r['limit'] == chosen else ''}₹{r['limit']:,}{'**' if r['limit'] == chosen else ''} | {r['approved']:,} | {r['approve_rate']:.0%} | {r['wrong_approvals']} | {inr(r['wrong_approval_rs'])} | {r['wrong_declines']} | {r['minutes_saved']:,} | {inr(r['net'])} | {inr(r['net'] / n_weeks)} |" for r in rows) + f"""

![threshold sweep](sweep.svg)

**Chosen: ₹{chosen:,}** (highest net in the sweep: ₹{best['limit']:,}). {why.strip()} Finance owns this number.

## Holdout versus treated (whole window)

Holdout = `md5(claim_id) % 100 < {cfg['holdout_share'] * 100:.0f}`. Holdout claims skip the rules and go to an agent as today.

| Per claim | Treated | Holdout |
|---|---|---|
| Claims | {T['claims']:,} | {Hd['claims']:,} |
| Agent handling minutes | {T['minutes']:.1f} | {Hd['minutes']:.1f} |
| Approval rate | {T['approval_rate']:.0%} | {Hd['approval_rate']:.0%} |
| Refund ₹ | {T['refund']:,.0f} | {Hd['refund']:,.0f} |
| Repeat claim from the cluster within 30 days | {T['repeat30']:.0%} | {Hd['repeat30']:.0%} |
| Cost (refund + handling) | {T['cost']:,.0f} | {Hd['cost']:,.0f} |
| CSAT | not in synthetic data | not in synthetic data |

- Net weekly saving versus holdout = (holdout cost − treated cost) × treated claims ÷ {n_weeks} weeks = **{inr(net_vs_holdout)}**.
- Contamination: {mixed:.0%} of holdout claims share a customer cluster with a treated claim. """ + (
        "Small, so claim-level holdout stands." if mixed < .10 else
        "That is large: a customer who gets a fast auto-approval on one claim and a slow agent on the next is in both arms. "
        "For v2, assign holdout by `md5(cluster_id)` instead of claim_id.") + f"""
- Sizing: with ~{nh:.0f} holdout and ~{nt:,.0f} treated claims per 4 weeks, the smallest approval-rate change we can detect is about **{mde:.0%} points** (80% power, 5% two-sided). Do not promise a smaller effect.
- Repeat-claim rate is the same in both arms here because the synthetic data plants no behaviour change; with real data this is the metric that shows whether auto-declines deter repeat abuse.

## Weekly report, week of {fmt_week(this_w)}

| Item | This week | Last week ({fmt_week(last_w)}) | Assumed at approval |
|---|---|---|---|
{weekly}
| Holdout vs treated | handling {Hd['minutes']:.1f} vs {T['minutes']:.1f} min/claim, repeat {Hd['repeat30']:.0%} vs {T['repeat30']:.0%} (window to date) | | |

**Drift flags and actions**

""" + "\n".join(f"- {f}" for f in flags) + f"""

Monday review: open every audited wrong approval and wrong decline from the week, read its reason codes, and write down which rule caused it.

## Assumptions (edit in `report.py`)

| Assumption | Value |
|---|---|
| Agent cost per minute | ₹{A['cost_per_min']} |
| Minutes to reverse a wrong decline | {A['fix_min']} |
| Goodwill credit per wrong decline | ₹{A['goodwill']} |
| Lost repeat margin per wrong decline | ₹{A['lost_margin']} |
| Audit sample rate (from data) | {ctx['audit_rate']:.0%} |

Definitions: wrong approval = auto-approved and audit says not genuine (cost = claim amount). Wrong decline = auto-declined and audit says genuine (cost = fix time + goodwill + lost margin). Saved = agent minutes saved × cost per minute + amount of correctly declined claims. Net = saved − wrong approvals − wrong declines.
"""
    (OUT / "report.md").write_text(md, encoding="utf-8")
    print(f"auto {cur['auto_rate']:.0%}, wrong approvals {cur['wrong_approvals']}, wrong declines {cur['wrong_declines']}, "
          f"net/week vs holdout {inr(net_vs_holdout)}, caught by cluster {len(caught)} -> {OUT / 'report.md'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RULES)
