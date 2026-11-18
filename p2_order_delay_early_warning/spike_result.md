# Spike test: flag + list job

_Synthetic. sqlite on one laptop core, 250 orders/day at 1x, one 08:00 IST run after 35 days of history. Cut-off budget: list ready 30 min after the run starts._

| Volume | Orders/day | Open orders | Flagged | Listed | Run time | Ready by cut-off |
|---|---|---|---|---|---|---|
| 1x | 250 | 1,123 | 398 | 359 | 0.10s | yes |
| 2x | 500 | 2,235 | 865 | 781 | 0.22s | yes |
| 3x | 750 | 3,371 | 1,289 | 1,148 | 0.34s | yes |
| 10x | 2,500 | 11,352 | 4,444 | 3,953 | 1.32s | yes |

## Result

- No step failed or timed out at any volume. Run time grows about linearly with open orders (worst 0.13s per 1x).
- Straight-line, the single run would hit the 30-min budget at about **13,648x** volume. Long before that, the real limits are elsewhere: the full re-read of 30 days of scans each run, and the messaging provider's send rate for a list this size.
- When to change it: once a run takes more than ~5 min, or open orders pass ~10x today, read scans incrementally (only orders with a new scan or a promise date inside 3 days) and batch the list writes. Until then the full re-read is the simpler and safer choice, because it picks up late courier edits for free.
- Flag share by volume: 1x 35.4%, 2x 38.7%, 3x 38.2%, 10x 39.1%. A pure volume spike does not move the share, so the 'flag rate jumps' monitor stays quiet on a sale day and keeps its meaning: a jump points to a data problem.
