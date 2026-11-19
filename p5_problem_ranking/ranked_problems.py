"""Where does CX break, and what does it cost? Ranked list sized in rupees.
Run: py p5_problem_ranking/ranked_problems.py  ->  p5_problem_ranking/ranked_problems.md
SYNTHETIC data. All cost assumptions are in ASSUME below - change them, rerun, ranking updates.
"""
import pathlib
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252, can't print rupee sign

HERE = pathlib.Path(__file__).resolve().parent
con = sqlite3.connect(HERE.parent / "data" / "cx.db")
con.row_factory = sqlite3.Row

ASSUME = dict(contact_cost=55,      # Rs per ticket handled (agent time)
              margin=0.22,          # gross margin on a lost / unrealised sale
              rto_logistics=110,    # Rs wasted shipping on a returned-to-origin order
              return_handling=140,  # Rs reverse pickup + inspection per return
              return_writeoff=0.15, # share of returned value that cannot be resold
              months=3)             # data window length, to quote Rs per month
CUT = "2026-09-01"  # repeat is observed over 60 days, so only orders up to here are 'eligible'

# One row per delivery outcome. Window function LEAD() finds each customer's next order.
OUTCOME_SQL = """
WITH o AS (
  SELECT *,
    CASE status WHEN 'cancelled_oos' THEN 'oos' WHEN 'rto' THEN 'rto'
         WHEN 'delivered' THEN CASE WHEN delivered_date > promised_date THEN 'late' ELSE 'on_time' END END AS grp,
    CASE WHEN julianday(LEAD(order_date) OVER (PARTITION BY customer_id ORDER BY order_date))
              - julianday(order_date) <= 60 THEN 1 ELSE 0 END AS repeat60
  FROM orders),
t AS (SELECT order_id, COUNT(*) tix FROM tickets WHERE order_id IS NOT NULL GROUP BY order_id),
r AS (SELECT order_id, SUM(amount) refund FROM refunds GROUP BY order_id)
SELECT o.grp, COUNT(*) n, AVG(o.value) avg_value,
       SUM(COALESCE(t.tix, 0)) tix, SUM(COALESCE(r.refund, 0)) refund,
       SUM(o.order_date <= :cut) elig, SUM(o.repeat60 * (o.order_date <= :cut)) rep
FROM o LEFT JOIN t USING (order_id) LEFT JOIN r USING (order_id)
GROUP BY o.grp"""

LANE_SQL = """
SELECT lane, courier, COUNT(*) n,
       SUM(delivered_date > promised_date) late,
       ROUND(100.0 * SUM(delivered_date > promised_date) / COUNT(*), 1) late_pct
FROM orders WHERE status = 'delivered' GROUP BY lane, courier ORDER BY late_pct DESC"""

RETURN_SQL = """
WITH s AS (
  SELECT o.category, o.sku, COUNT(*) orders, SUM(rt.return_id IS NOT NULL) rets, AVG(o.value) avg_value
  FROM orders o LEFT JOIN returns rt USING (order_id) WHERE o.status = 'delivered'
  GROUP BY o.category, o.sku),
base AS (SELECT category, 1.0 * SUM(rets) / SUM(orders) rate FROM s GROUP BY category)
SELECT s.category, s.sku, s.orders, s.rets, 1.0 * s.rets / s.orders rate,
       (SELECT 1.0 * SUM(s2.rets) / SUM(s2.orders) FROM s s2
         WHERE s2.category = s.category AND s2.sku <> s.sku) peer_rate, s.avg_value
FROM s ORDER BY rate DESC LIMIT 1"""

COVERAGE_SQL = """
SELECT 'tickets with an order_id' k, ROUND(100.0 * SUM(order_id IS NOT NULL) / COUNT(*), 1) v FROM tickets
UNION ALL SELECT 'refunds joined to an order', ROUND(100.0 * COUNT(o.order_id) / COUNT(*), 1)
          FROM refunds r LEFT JOIN orders o USING (order_id)
UNION ALL SELECT 'returns joined to an order', ROUND(100.0 * COUNT(o.order_id) / COUNT(*), 1)
          FROM returns r LEFT JOIN orders o USING (order_id)"""


def inr(x):
    return f"₹{x / 1e5:.1f}L" if abs(x) >= 1e5 else f"₹{x:,.0f}"


A = ASSUME
g = {r["grp"]: r for r in con.execute(OUTCOME_SQL, {"cut": CUT})}
base_rep = g["on_time"]["rep"] / g["on_time"]["elig"]


def lost_repeat(r):  # repeat-rate gap vs on-time orders x orders x value x margin
    gap = max(0, base_rep - r["rep"] / r["elig"])
    return gap * r["n"] * r["avg_value"] * A["margin"], gap


rows = []
# 1 Late delivery: goodwill credits + extra contacts + lost repeat margin
l = g["late"]; lr, gap = lost_repeat(l)
rows.append(("Late deliveries (EDD missed)", l["n"], l["refund"] + l["tix"] * A["contact_cost"] + lr,
             f"credits {inr(l['refund'])}, {l['tix']} tickets, repeat rate {gap * 100:.0f} pts lower", "Supply chain"))
# 2 Out of stock after order: lost margin + contacts + lost repeat
o = g["oos"]; lr, gap = lost_repeat(o)
rows.append(("Out of stock after order", o["n"], o["n"] * o["avg_value"] * A["margin"] + o["tix"] * A["contact_cost"] + lr,
             f"lost sales margin, {o['tix']} tickets, repeat {gap * 100:.0f} pts lower", "Category / inventory"))
# 3 Wrong address -> RTO
x = g["rto"]; lr, gap = lost_repeat(x)
rows.append(("Wrong address (return to origin)", x["n"],
             x["n"] * (A["rto_logistics"] + x["avg_value"] * A["margin"]) + x["tix"] * A["contact_cost"] + lr,
             f"wasted shipping, lost sale, {x['tix']} tickets", "Product (address capture) / CX"))
# 4 Returns outlier SKU: excess returns over its category peers
rt = con.execute(RETURN_SQL).fetchone()
excess = max(0, rt["rets"] - rt["peer_rate"] * rt["orders"])
rows.append((f"Returns outlier: {rt['sku']} ({rt['category']})", int(excess),
             excess * (A["return_handling"] + rt["avg_value"] * A["return_writeoff"] + A["contact_cost"]),
             f"return rate {rt['rate'] * 100:.0f}% vs {rt['peer_rate'] * 100:.0f}% for peers", "Category / product page"))
rows.sort(key=lambda r: -r[2])
total = sum(r[2] for r in rows)

lanes = con.execute(LANE_SQL).fetchall()
late_total = sum(r["late"] for r in lanes)
top3 = lanes[:3]
top3_share = sum(r["late"] for r in top3) / late_total * 100
top3_orders = sum(r["n"] for r in top3) / sum(r["n"] for r in lanes) * 100
sz = con.execute("SELECT SUM(reason='size') s, COUNT(*) c FROM returns WHERE sku=?", (rt["sku"],)).fetchone()

md = ["# Where CX breaks: ranked and sized in rupees", "",
      "_Synthetic data, same structure as production. Assumptions at the bottom; edit and rerun._", "",
      f"**Total sized: {inr(total)} over {A['months']} months (~{inr(total / A['months'])}/month).**", "",
      "| # | Problem | Orders | Cost (₹) | Share | What's in the number | Owner |", "|---|---|---|---|---|---|---|"]
for i, (name, n, cost, why, owner) in enumerate(rows, 1):
    md.append(f"| {i} | {name} | {n:,} | {inr(cost)} | {cost / total * 100:.0f}% | {why} | {owner} |")
md += ["", "## Finding behind #1: the delay sits in a few lanes",
       f"The 3 worst lanes by late rate ({', '.join(r['lane'] for r in top3)}, all on "
       f"{'/'.join(sorted({r['courier'] for r in top3}))}) carry **{top3_share:.0f}% of all late deliveries** "
       f"on only {top3_orders:.0f}% of delivered orders.", "",
       "| Lane | Courier | Delivered | Late | Late % |", "|---|---|---|---|---|"]
md += [f"| {r['lane']} | {r['courier']} | {r['n']:,} | {r['late']:,} | {r['late_pct']}% |" for r in lanes]
md += ["", f"## Finding behind #4: {rt['sku']} returns are a size problem",
       f"{sz['s'] / sz['c'] * 100:.0f}% of its returns cite size. Fix: size chart or fit guidance on the product page, "
       "agreed with category and product.", "",
       "## Data coverage (say how much each join covers)"]
md += [f"- {r['k']}: **{r['v']}%**" for r in con.execute(COVERAGE_SQL)]
md += ["- Repeat purchase measured over 60 days, so only orders up to " + CUT + " are counted (no censored orders).",
       "", "## Assumptions", ""]
md += [f"- `{k}` = {v}" for k, v in A.items()]
out = HERE / "ranked_problems.md"
out.write_text("\n".join(md), encoding="utf-8")
print("\n".join(md))
