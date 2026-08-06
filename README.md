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
| `scoring.py` | Frechet bounds and per-fill verdict (written by Eden) |
| `calibration.py` | price versus realized outcome (written by Eden) |
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
.venv/bin/python -m pytest            # 104 passing, 20 red by design
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

## What the tape contains

Collected 2026-07-30 04:14 to 2026-08-05 21:31 UTC.

| | |
|---|---|
| distinct parlay combinations | 1,877,284 |
| fills | 3,754,710 |
| distinct legs | 40,261 |
| settlement confirmed | 96.7% (1,825,794 finalized) |
| parlays that hit | 11.6% of resolved fills |

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

What remains is a smaller and more honest limit: about a quarter of fills have
at least one leg with no quote activity within two minutes.

| tolerance | scorable | no bar near fill | one-sided book |
|---|---|---|---|
| ±60s | 66.0% | 32.3% | 1.7% |
| ±120s | 74.4% | 22.8% | 2.8% |
| ±300s | 82.0% | 15.2% | 2.9% |
| ±600s | 85.7% | 10.5% | 3.9% |

Widening the window buys coverage and sells accuracy. ±120s is the reported
figure because these are live in-game legs, where a ten-minute-old leg price is
the original staleness problem in a smaller costume.

## Limitations

- Six days, one exchange, mostly one sport. Depth without breadth.
- The rig sees prints, not the RFQ auction. It cannot see quotes that lost, so
  a price outside the Frechet interval was not necessarily capturable.
- The unscorable remainder skews toward illiquid legs, which is where
  mispricing is most likely, so measured rates may understate the total.
- Accepted quoters pay the standard maker fee, `round_up(0.0175 * C * P *
  (1-P))`. Edge claims in `report.py` are net of it.
