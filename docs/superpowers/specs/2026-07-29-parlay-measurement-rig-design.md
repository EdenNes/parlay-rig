# Parlay measurement rig: design

Date: 2026-07-29
Status: approved by Eden (chat session, 2026-07-29 evening)
Owner: Eden Nesvisky. Plumbing built with Claude; scoring module written by Eden.

## Purpose

Passive measurement of parlay (combo) pricing quality on Kalshi, from the market maker's
side of the trade. The rig answers one question with data: how far do real parlay fills
sit from coherent prices today? It collects the public combo trade tape and leg prices,
scores every fill against the Frechet ceiling and the independence price, and produces a
daily report. It quotes nothing, trades nothing, and needs no credentials.

Context: research project assigned by Bill Brophy (Hard Eight Trading) on 2026-07-29.
The rig is the "build as you go" artifact for the call with Bill, Manil, and Anh targeted
for the week of Aug 10. The research brief behind it lives at
https://claude.ai/code/artifact/b3b2433b-1481-4158-afcb-8d8391c3c013.

## Scope

In scope (phase 1, this week):
- Collector polling Kalshi's public trade API for multivariate (KXMVE) parlay markets,
  their trade tape, leg quotes at capture time, and per-series volume census rows.
- Scoring module (Eden's code): Frechet bounds, independence price, per-fill gaps.
- Daily markdown report.
- Deploy to the GCP weather-kalshi VM on cron.

In scope (phase 2, only after phase 1 is live and stable):
- backfill.py: score historical fills using candlestick leg prices, reusing scoring.py
  unchanged.

Out of scope:
- RFQ WebSocket listening (needs auth investigation, parked).
- Polymarket (Cato-blocked from the laptop; revisit on GCP later).
- Anything that places orders or touches account credentials.

## Architecture

Three small programs around one SQLite file.

    cron on the GCP weather-kalshi VM
     |- every 2 min -> collector.py --raw facts--> data.db (SQLite, WAL mode)
     |- nightly     -> report.py   --reads db, imports scoring.py--> reports/YYYY-MM-DD.md
     phase 2:          backfill.py -- same scoring.py, candlestick leg prices --> data.db

Load-bearing decision: the collector stores raw facts only and never scores. Scoring
runs at report time by applying pure functions to stored rows. Rationale: the scoring
model will iterate, and re-scoring stored history is free while re-collecting missed
history is impossible.

## Components

kalshi.py (Claude, ~60 lines)
  Thin client for the public API at https://api.elections.kalshi.com/trade-api/v2.
  One requests session. Every call: 10 s timeout, 3 retries, exponential backoff
  (1 s, 2 s, 4 s). Three functions: markets_page(series, cursor), trades(ticker),
  market(ticker). Returns parsed JSON, raises on anything unrecoverable.

collector.py (Claude, ~150 lines)
  One polling cycle per invocation; cron is the scheduler. Cycle:
  1. Discover active parlay series from /multivariate_event_collections.
  2. For each series, page /markets newest-first until a page contains only tickers
     already stored; upsert market rows (legs come from mve_selected_legs).
  3. For any market whose volume_fp rose since the stored value, fetch its trade tape
     and insert new fills (trade_id primary key makes this deduplicating).
  4. For each new fill, fetch each leg market once per cycle and insert a leg_quotes
     snapshot (yes bid, yes ask, timestamp).
  5. Append one census row per series (market count, total volume).

scoring.py (Eden, ~40 lines + tests)
  Pure functions, no I/O:
  - leg_prob(side, yes_price): probability implied by a leg given which side the
    parlay takes.
  - frechet_ceiling(leg_probs): min of the leg probabilities.
  - frechet_floor(leg_probs): max(0, sum(leg_probs) - (n - 1)).
  - independence_price(leg_probs): product of the leg probabilities.
  - score_fill(fill_price, leg_probs): dict with ceiling, floor, independence price,
    gap to ceiling, gap to independence, and a coherent yes/no flag.

report.py (Claude, ~120 lines)
  Joins each fill to its nearest-in-time leg quotes (indexed lookup), applies
  scoring.py to every fill, writes reports/YYYY-MM-DD.md: fills captured, percent of
  fills violating the Frechet ceiling, average gap to independence by series and by
  day, and the volume census series.

backfill.py (phase 2)
  Same join and same scoring.py; leg prices come from Kalshi candlestick history
  instead of live snapshots.

## Data model

SQLite, WAL mode, busy_timeout set. Four tables:

    markets(ticker TEXT PRIMARY KEY, series TEXT, event_ticker TEXT,
            collection TEXT, legs_json TEXT, created_ts TEXT, close_ts TEXT,
            volume_fp REAL, oi_fp REAL, status TEXT, result TEXT,
            last_seen_ts TEXT)
    trades(trade_id TEXT PRIMARY KEY, ticker TEXT, ts TEXT,
           yes_price REAL, count_fp REAL, taker_side TEXT)
    leg_quotes(leg_ticker TEXT, ts TEXT, yes_bid REAL, yes_ask REAL)
      with index (leg_ticker, ts)
    census(ts TEXT, series TEXT, n_markets INTEGER, total_volume_fp REAL)

No separate cursor table: the collector's resume point is derivable (newest stored
created_ts per series), and volume-rise detection uses markets.volume_fp.

## Error handling

- Timeouts on every request; retries with exponential backoff; specific exceptions only.
- On HTTP 429, log and end the cycle early; the next tick continues.
- Idempotent writes (insert-or-ignore, upserts), so a crashed cycle is repeated
  harmlessly by the next cron tick.
- One log line per cycle to a plain file: start time, counts written, duration, errors.
- WAL mode plus busy_timeout so the nightly report and a collector tick never block
  each other.

## Known limits (stated in README)

- Leg quotes lag fills by up to about 2 minutes (poll cadence).
- Scalar legs (outcomes multiplied) are excluded from v1 scoring; binary legs only.
- Public tape only: RFQ quotes that never filled are invisible to the rig.

## Testing

- pytest on scoring.py. Golden cases: the study's incoherent quote (legs 82c and 14c,
  fill at 80c must flag incoherent), floor edge cases, side conversion, two-leg
  independence. One assertion per test, behavior over implementation.
- report.py join tested against a small fixture database.
- One live smoke test for kalshi.py (single page fetch), marked slow.

## Deploy

- Dev on the laptop (Kalshi API confirmed reachable from it 2026-07-29).
- rsync project to the weather-kalshi VM; two cron lines: collector every 2 minutes,
  report at 23:55.
- Repo: /Users/eden/Documents/code/parlay-rig-2026-07-29, private GitHub repo
  parlay-rig. Feature branches, squash-merge, no direct commits to main after the
  bootstrap commit. No secrets in the repo; every endpoint is public.

## Success criteria

By the Bill/Manil/Anh call (week of Aug 10):
- Collector has run unattended on the VM for multiple consecutive days.
- Daily reports exist with real scored fills.
- Eden can demo the collector live in a terminal and walk the report.
- Eden can explain every component in this document unaided.

## Explain it in five sentences (for the call)

1. A cron job on my server polls Kalshi's public API every two minutes and appends
   every new parlay market, fill, and leg quote to a SQLite database.
2. Collection is dumb and append-only; nothing is scored at write time.
3. A separate pure-math module computes each fill's Frechet ceiling, independence
   price, and the gaps to both, applied at report time, so I can iterate on the model
   and re-score all history in seconds.
4. A nightly report summarizes how much of the flow trades outside coherent bounds,
   which is a direct read on how much edge is left for a disciplined quoter.
5. The same math module will re-score months of historical fills from candlestick
   data, so the live window and the backfill cross-check each other.
