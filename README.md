# parlay-rig

A measurement rig for Kalshi multi-event contracts (parlays, or "combos"). It
records the public trade tape, reconstructs what each parlay's legs were priced
at in the minute each fill happened, and asks two questions of the result:

1. **Coherence.** Did the parlay print at a price that no probability
   assignment consistent with its legs could justify?
2. **Calibration.** Across settled fills, does a parlay that printed at 12c
   actually hit about 12% of the time?

Built for a research question from Hard Eight: is there real market-maker edge
in responding to parlay RFQs, and does the mispricing reported in a February
2026 preprint (arXiv 2603.22596) still exist in current flow.

## Why the two questions are different

Coherence compares a price to other prices. Kalshi publishes each combo's exact
legs (`mve_selected_legs`), and the joint probability of several events is
bounded by the Frechet inequalities regardless of how those events correlate:

    floor(p)   = max(0, sum(p) - (n - 1))
    ceiling(p) = min(p)

A parlay cannot hit more often than its least likely leg, and cannot hit less
often than the floor. So a fill above the ceiling is money the seller cannot
lose under any dependence structure, and a fill below the floor is the reverse.
No settlement data is needed, and no view on correlation is needed. That is why
it is called model-free.

Calibration compares a price to an outcome. It needs settlement and does not
need leg prices at all. It is the test that converts to profit and loss per
contract, and it is the direct replication of the preprint's claim.

Neither substitutes for the other.

## Layout

| file | role |
|---|---|
| `db.py` | schema and connection for the collected tape |
| `kalshi.py` | HTTP client: throttling, 429 handling, batch lookups, candlesticks |
| `collector.py` | phase 1, polls the public tape and stores parlays, fills, legs |
| `settle.py` | re-queries every stored market for its final outcome |
| `legs.py` | derives, then narrows, the price window each leg needs |
| `backfill.py` | pulls one-minute bid/ask history per leg over that window |
| `coverage.py` | how many fills can be scored, and why the rest cannot |
| `scoring.py` | Frechet bounds and per-fill verdict |
| `calibration.py` | price versus realized outcome |
| `report.py` | joins it together and writes the markdown report |

## Data model

Two SQLite databases. `data/rig.db` is the collected tape and is treated as
read-only once collection stops. Everything derived is written to
`data/derived.db`, so no analysis can corrupt the record.

### rig.db

- **markets** one row per parlay combination. `legs_json` holds the exact legs
  with each leg's `market_ticker` and `side`. `status` and `result` are as of
  first sighting and are stale by design, which is why `settle.py` exists.
- **trades** one row per fill: `trade_id`, `ticker`, `ts`, `yes_price`,
  `count_fp`, `taker_side`.
- **leg_quotes** the phase 1 leg snapshots, taken once per collection cycle.
  Superseded by `leg_candles`, kept for comparison.
- **census** per-cycle market and volume counts by series.

### derived.db

- **settlements** `ticker`, `status`, `result`, `close_ts`, `fetched_ts`. A row
  exists for every ticker asked about, with a null status if the exchange did
  not return it, so unconfirmable markets are counted rather than dropped.
- **leg_windows** `leg_ticker`, `start_ts`, `end_ts`, `n_parlays`.
- **leg_candles** `leg_ticker`, `ts`, `yes_bid`, `yes_ask`, one row per minute
  with quote activity.
- **leg_backfilled** which legs are done, how many requests each took, and any
  error, so progress is never mistaken for a data quality problem.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install requests pytest
.venv/bin/python -m pytest            # 124 passing
```

Stages, in order. Each is resumable and safe to re-run.

```bash
python3 collector.py     # phase 1, cron every 2 minutes (stopped 2026-08-05)
python3 settle.py        # outcomes for every stored market
python3 legs.py          # build leg windows, then narrow them
python3 backfill.py      # one-minute leg price history
python3 coverage.py 3000 # what fraction of fills are scorable
python3 report.py        # the coherence report
```

Calibration has no CLI; it streams the settled tape through `calibrate`:

```python
import settle, calibration
conn = settle.connect()
cur = conn.execute(
    "SELECT t.yes_price AS price, s.result AS result "
    "FROM rig.trades t JOIN settlements s ON s.ticker = t.ticker "
    "WHERE s.result IN ('yes','no')")
for row in calibration.calibrate(cur):
    print(row)
```

## What the tape contains

Collected 2026-07-30 04:14 to 2026-08-05 21:31 UTC.

| | |
|---|---|
| distinct parlay combinations | 1,877,284 |
| fills | 3,754,710 |
| distinct legs | 40,261 |
| settlement confirmed | 96.7% (1,825,794 finalized) |
| parlay combinations that settled yes | 11.6% of resolved markets |
| settled fills that hit | 16.7% |

## The measurement problem this rig had, and the fix

Phase 1 snapshotted each leg's quote once per collection cycle. As volume grew,
cycles stretched past an hour, so a fill was being scored against leg prices
from up to an hour later. On live in-game markets that is not noise, it is bias
in one direction: legs that had effectively resolved by snapshot time made every
parlay look cheap. 30.5% of those snapshots were empty books quoted `0.00 /
1.00`, whose midpoint is a manufactured 0.5.

The fix is a point-in-time join. Kalshi publishes one-minute candlestick history
per market, so each leg is priced at the fill's own minute instead of whenever
the collector happened to look. Empty books fell from 30.5% to 2.8%.

What remains is a smaller and more honest limit: about a third of fills have
at least one leg with no quote activity within two minutes of the fill.
Measured on the complete backfilled leg set, 20,000 random fills per window:

| tolerance | scorable | no bar near fill | one-sided book |
|-------|-------|-------|------|
| ±60s  | 59.3% | 37.8% | 2.9% |
| ±120s | 67.6% | 29.2% | 3.2% |
| ±300s | 77.0% | 18.8% | 4.2% |
| ±600s | 82.4% | 12.7% | 4.8% |

Widening the window buys coverage and sells accuracy. ±120s is the reported
figure because these are live in-game legs, where a ten-minute-old leg price is
the original staleness problem in a smaller costume. The two failure modes move
in opposite directions as the window widens: missing bars are a search-radius
problem and shrink, while one-sided books are a market-quality problem and grow,
because the marginal legs found at wider radii are thinner markets.

## Results

### Coherence: nothing there

Of 40,000 fills drawn, 27,109 were scorable at ±120s. Pricing each leg at its
mid, 233 fills (0.86%) printed outside the Frechet interval. Netting the maker
fee and pricing each leg on the side least favourable to the claim: 199 above
the ceiling (0.73%) and 149 below the floor (0.55%), median 4 legs per fill,
median Frechet interval width 0.405.

The 20%-above-ceiling rate measured from the phase 1 snapshots was entirely a
staleness artifact. With point-in-time leg prices there is no meaningful
model-free mispricing in this tape. `report.py` reproduces this.

### Calibration: a small, real, fee-sized seller edge

Across all 3,652,705 settled fills, one number, pre-registered as the primary
test before the data was seen:

    mean price 17.03c   realized hit rate 16.70%   seller edge +0.33c/contract

The 95% Wilson interval on the hit rate is (16.67%, 16.74%); the mean price
sits above it, so the edge is distinguishable from zero. It is not
distinguishable from the cost of doing business: the standard maker fee at
these prices is roughly a quarter cent per contract, which consumes most of
the gross edge. Each fill counts once, unweighted by contract size.

Per-bucket results are exploratory, not pre-registered:

| price bucket | n | mean price | hit rate (95% Wilson) | seller edge |
|---|---|---|---|---|
| 0 to 1c | 632,782 | 0.41c | 0.17% (0.16, 0.18) | +0.24c |
| 1 to 2c | 296,759 | 1.39c | 0.91% (0.88, 0.95) | +0.48c |
| 2 to 5c | 506,996 | 3.30c | 2.74% (2.69, 2.78) | +0.56c |
| 5 to 10c | 498,100 | 7.23c | 6.17% (6.10, 6.24) | +1.06c |
| 10 to 20c | 577,747 | 14.50c | 13.63% (13.54, 13.72) | +0.87c |
| 20 to 35c | 526,414 | 26.81c | 26.53% (26.41, 26.64) | +0.29c |
| 35 to 50c | 325,796 | 41.75c | 41.74% (41.57, 41.91) | +0.01c |
| 50c and up | 288,111 | 70.01c | 71.99% (71.83, 72.15) | -1.98c |

The shape is the favorite-longshot bias: cheap parlays hit less often than
their price implies (sellers earn), expensive ones hit more often (sellers
lose). Where the volume is, below 20c, the seller side collected between a
quarter cent and a cent per contract before fees.

## Limitations

- Six days, one exchange, mostly one sport. Depth without breadth.
- The rig sees prints, not the RFQ auction. It cannot see quotes that lost, so
  a price outside the Frechet interval was not necessarily capturable.
- The unscorable remainder skews toward illiquid legs, which is where
  mispricing is most likely, so measured rates may understate the total.
- Accepted quoters pay the standard maker fee, `round_up(0.0175 * C * P *
  (1-P))`. Edge claims in `report.py` are net of it.
