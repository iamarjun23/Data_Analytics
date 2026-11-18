# Coverage report (P4 customer profile, data as of 2026-11-15)

SYNTHETIC data. Published with every build of the profile.

| Source | Records | Matched | Matched % | Medium-confidence (tier C) | Unmatched reason |
| --- | ---: | ---: | ---: | ---: | --- |
| vet_consults | 855 | 698 | 81.6% | 0 | phone not on any account (157) |
| calls | 6,221 | 5,782 | 92.9% | 1 | phone not on any account (439) |
| clinic_bookings | 826 | 812 | 98.3% | 0 | shared phone, not used to link (12); phone not on any account (2) |
| accounts | 5,516 | 5,516 | 100.0% | 1 | - |
| orders | 47,120 | 47,120 | 100.0% | 2 | - |
| pharmacy_rx | 616 | 616 | 100.0% | 0 | - |
| tickets | 3,429 | 3,429 | 100.0% | 0 | - |

| Check | Value |
| --- | ---: |
| Customers | 5,001 |
| Customers with more than one phone | 1,068 |
| Customers with more than one account | 515 |
| Customers whose accounts were joined only by tier C | 1 |
| Clusters flagged for review (> 5 accounts or phones) | 0 |
| Shared phones not used to link | 1 |
| Tier D candidates waiting for review | 182 |
| Account-pair precision vs planted truth | 100.0% |
| Account-pair recall vs planted truth | 99.8% (1 same-person pairs missed) |

## Clinic duplicate bookings

Active bookings for the same customer, pet and specialty in the same week. Every group goes to an
agent to confirm; nothing is cancelled automatically.

| Week | Duplicate groups | Extra slots that could be freed |
| --- | ---: | ---: |
| 2026-W46 | 9 | 9 |
| 2026-W47 | 6 | 6 |
| 2026-W48 | 9 | 9 |

Groups that are really two different people (would be wrongly contacted): **0**.
