# Where CX breaks: ranked and sized in rupees

_Synthetic data, same structure as production. Assumptions at the bottom; edit and rerun._

**Total sized: ₹11.2L over 3 months (~₹3.7L/month).**

| # | Problem | Orders | Cost (₹) | Share | What's in the number | Owner |
|---|---|---|---|---|---|---|
| 1 | Late deliveries (EDD missed) | 3,264 | ₹4.7L | 42% | credits ₹1.3L, 2346 tickets, repeat rate 25 pts lower | Supply chain |
| 2 | Out of stock after order | 755 | ₹3.3L | 29% | lost sales margin, 332 tickets, repeat 27 pts lower | Category / inventory |
| 3 | Wrong address (return to origin) | 555 | ₹2.6L | 23% | wasted shipping, lost sale, 373 tickets | Product (address capture) / CX |
| 4 | Returns outlier: BED-L (Accessories) | 141 | ₹59,338 | 5% | return rate 31% vs 5% for peers | Category / product page |

## Finding behind #1: the delay sits in a few lanes
The 3 worst lanes by late rate (BLR-NE, BLR-KOL, BLR-DEL, all on CourierX) carry **62% of all late deliveries** on only 26% of delivered orders.

| Lane | Courier | Delivered | Late | Late % |
|---|---|---|---|---|
| BLR-NE | CourierX | 682 | 410 | 60.1% |
| BLR-KOL | CourierX | 1,274 | 713 | 56.0% |
| BLR-DEL | CourierX | 1,981 | 913 | 46.1% |
| BLR-PUN | CourierB | 1,020 | 140 | 13.7% |
| BLR-MUM | CourierB | 1,651 | 212 | 12.8% |
| BLR-CHN | CourierA | 1,715 | 200 | 11.7% |
| BLR-HYD | CourierA | 1,993 | 233 | 11.7% |
| BLR-BLR | Express | 5,032 | 443 | 8.8% |

## Finding behind #4: BED-L returns are a size problem
72% of its returns cite size. Fix: size chart or fit guidance on the product page, agreed with category and product.

## Data coverage (say how much each join covers)
- tickets with an order_id: **96.6%**
- refunds joined to an order: **100.0%**
- returns joined to an order: **100.0%**
- Repeat purchase measured over 60 days, so only orders up to 2026-09-01 are counted (no censored orders).

## Assumptions

- `contact_cost` = 55
- `margin` = 0.22
- `rto_logistics` = 110
- `return_handling` = 140
- `return_writeoff` = 0.15
- `months` = 3