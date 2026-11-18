# P0: Where CX breaks, ranked and sized in rupees

_Synthetic data, same structure as production. No real customer data, thresholds or internal numbers._

| | |
| --- | --- |
| **Status** | Built |
| **Output** | [ranked_problems.md](ranked_problems.md) |
| **Reads** | the shared dataset `data/cx.db`, built by [../common/gen_data.py](../common/gen_data.py) |
| **Check** | runs in CI on every push ([../.github/workflows/ci.yml](../.github/workflows/ci.yml)) |

**Start here.** This is the analysis that comes *before* the five projects. It answers "where does CX break, and what does each problem cost?" The five projects are what you build once you know the answer.

## Problem

Every CX team has a long list of complaints: late deliveries, out-of-stock items, wrong addresses, returns. Without a cost on each one, the loudest problem gets fixed first, not the most expensive one. This script ranks the problems by rupee cost, names an owner for each, and shows the finding behind the biggest ones.

## Approach

1. **One row per delivery outcome.** Each order is classed as on time, late, out of stock after order (OOS), or returned to origin (RTO). A window function (`LEAD()`) finds each customer's next order, so repeat purchase can be compared across outcomes.
2. **Cost each problem in rupees** from parts you can defend in a meeting:
   - agent time on the tickets it causes (`contact_cost` per ticket),
   - goodwill credits and refunds paid,
   - lost margin on sales that never happened (`margin`),
   - wasted shipping on RTOs (`rto_logistics`),
   - return handling and write-off on unsellable returns.
3. **Avoid a censoring bug.** Repeat purchase is measured over 60 days, so only orders placed up to 1 Sep 2026 are counted. Orders placed later have not had 60 days to repeat yet.
4. **Report coverage for every join.** How many tickets have an order id, and how many refunds and returns join to an order. A number without its coverage is not trustworthy.
5. **Keep assumptions in one editable dict** (`ASSUME` at the top of the script). Change a cost and rerun, and the ranking updates.

## Result (3 months, Aug to Oct 2026)

**Total sized: ₹11.2L over 3 months (about ₹3.7L a month).**

| # | Problem | Orders | Cost | Share | Owner | Where it goes next |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Late deliveries (EDD missed) | 3,264 | ₹4.7L | 42% | Supply chain | [P2](../p2_order_delay_early_warning/README.md) warns customers early; [P1](../p1_refund_decisions/README.md) settles the late claims |
| 2 | Out of stock after order | 755 | ₹3.3L | 29% | Category / inventory | [P2](../p2_order_delay_early_warning/README.md) offers wait, substitute or refund |
| 3 | Wrong address (RTO) | 555 | ₹2.6L | 23% | Product / CX | [P2](../p2_order_delay_early_warning/README.md) asks for address confirmation before dispatch |
| 4 | Returns outlier: BED-L | 141 | ₹59k | 5% | Category / product page | Size chart on the product page |

**Finding behind #1:** the three worst lanes (BLR-NE, BLR-KOL, BLR-DEL, all on CourierX) carry **62% of all late deliveries on only 26% of delivered orders**. That points to a courier conversation, not a company-wide delivery problem.

**Finding behind #4:** 72% of BED-L returns cite size, against a 5% return rate for its peers. It is a product-page fix, not a CX one.

## Trade-off: how much do the assumptions matter?

Agent cost (₹55 a ticket) and margin (22%) are placeholders, so I reran the ranking with each one 30% lower and 30% higher (9 combinations):

| Contact cost \ Margin | 15.4% | 22% | 28.6% |
| --- | --- | --- | --- |
| **₹38.5** | 3.7L / 2.3L / 2.0L | 4.3L / 3.2L / 2.6L | 4.9L / 4.2L / 3.1L |
| **₹55** | 4.1L / 2.4L / 2.1L | **4.7L / 3.3L / 2.6L** | 5.3L / 4.2L / 3.2L |
| **₹71.5** | 4.5L / 2.4L / 2.1L | 5.1L / 3.4L / 2.7L | 5.7L / 4.3L / 3.2L |

_Cells: cost of late deliveries / out of stock / wrong address, in ₹._

**The order never changes**, so the recommendation does not depend on the placeholders. The sizes do: out of stock is the most sensitive to margin (₹2.3L to ₹4.3L), because most of its cost is lost sales. Quote it as a range until finance confirms the margin.

## Run it

Python 3.10+ and the standard library only. From the repo root:

```bash
python common/gen_data.py
```

```bash
python p0_problem_ranking/ranked_problems.py
```

The first command builds `data/cx.db` (fixed seed, so every run is identical). The second writes [ranked_problems.md](ranked_problems.md).

## What I would do next

- Move the sensitivity grid above into the script, so it is regenerated with every run.
- Add CSAT and repeat-purchase loss as a lifetime-value cost, not just 60-day repeat.
- Rerun monthly and track whether the share for #1 falls once P2 is live.
