"""Fetch each leg's one-minute price history over the window it is needed for.

The collector snapshotted leg quotes once per cycle, which drifted as far as
an hour from the fills being scored against them. Candlestick history is
addressed by timestamp instead, so a fill can be scored against its legs as
they were priced in that fill's own minute.
"""
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import kalshi

log = logging.getLogger("backfill")

# One-minute bars are capped to a one-day span per request; a wider range
# returns HTTP 400 "requested time span too large".
MAX_SPAN = 86400
PAD = 900
LOG_EVERY = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS leg_candles (
  leg_ticker TEXT,
  ts INTEGER,
  yes_bid REAL,
  yes_ask REAL,
  PRIMARY KEY (leg_ticker, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS leg_backfilled (
  leg_ticker TEXT PRIMARY KEY,
  chunks INTEGER,
  bars INTEGER,
  error TEXT,
  fetched_ts TEXT
);
"""


def _epoch(rfc3339: str) -> int:
    return int(datetime.fromisoformat(
        rfc3339.replace("Z", "+00:00")).timestamp())


def _chunks(start: int, end: int) -> List[Tuple[int, int]]:
    """Split a window into day-sized requests, padded so a fill at the very
    edge of the window still has a bar on either side of it."""
    lo, hi = start - PAD, end + PAD
    out = []
    while lo < hi:
        out.append((lo, min(lo + MAX_SPAN, hi)))
        lo += MAX_SPAN
    return out or [(lo, lo + MAX_SPAN)]


def _bars(leg: str, start: int, end: int) -> List[Dict[str, Any]]:
    series = leg.split("-")[0]
    page = kalshi.candlesticks(series, leg, start, end)
    return page.get("candlesticks", [])


def _close(bar: Dict[str, Any], side: str) -> float:
    return float((bar.get(side) or {}).get("close_dollars") or 0)


def _store(conn, leg: str, bars: List[Dict[str, Any]]) -> int:
    conn.executemany(
        "INSERT OR REPLACE INTO leg_candles (leg_ticker, ts, yes_bid, yes_ask)"
        " VALUES (?,?,?,?)",
        [(leg, b["end_period_ts"], _close(b, "yes_bid"), _close(b, "yes_ask"))
         for b in bars if b.get("end_period_ts")])
    return len(bars)


def _pending(conn) -> List[Tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT w.leg_ticker AS t, w.start_ts AS s, w.end_ts AS e "
        "FROM leg_windows w LEFT JOIN leg_backfilled b "
        "ON b.leg_ticker = w.leg_ticker WHERE b.leg_ticker IS NULL "
        "AND w.start_ts IS NOT NULL AND w.end_ts IS NOT NULL").fetchall()
    return [(r["t"], r["s"], r["e"]) for r in rows]


def _one_leg(conn, leg: str, start: str, end: str, now_iso: str) -> int:
    """Fetch and store one leg. A leg the exchange refuses is recorded with
    its error rather than dropped, so coverage stays countable."""
    bars = 0
    windows = _chunks(_epoch(start), _epoch(end))
    error = None
    for lo, hi in windows:
        try:
            bars += _store(conn, leg, _bars(leg, lo, hi))
        except kalshi.RateLimited:
            raise
        except (ValueError, KeyError, OSError) as exc:
            error = "%s: %s" % (type(exc).__name__, exc)
            break
    conn.execute(
        "INSERT OR REPLACE INTO leg_backfilled "
        "(leg_ticker, chunks, bars, error, fetched_ts) VALUES (?,?,?,?,?)",
        (leg, len(windows), bars, error, now_iso))
    conn.commit()
    return bars


def run(conn) -> Dict[str, int]:
    started = time.time()
    conn.executescript(SCHEMA)
    pending = _pending(conn)
    counts = {"legs": 0, "bars": 0, "errors": 0, "todo": len(pending)}
    log.info("start, %d legs pending", len(pending))
    for leg, start, end in pending:
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            counts["bars"] += _one_leg(conn, leg, start, end, now_iso)
        except kalshi.RateLimited:
            log.warning("rate limited after %d legs, stopping cleanly",
                        counts["legs"])
            break
        counts["legs"] += 1
        if counts["legs"] % LOG_EVERY == 0:
            log.info("%s, %.0fs elapsed", counts, time.time() - started)
    counts["errors"] = conn.execute(
        "SELECT COUNT(*) FROM leg_backfilled WHERE error IS NOT NULL"
    ).fetchone()[0]
    log.info("done in %.0fs: %s", time.time() - started, counts)
    return counts


def main() -> int:
    import settle
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(filename="logs/backfill.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    run(settle.connect())
    return 0


if __name__ == "__main__":
    sys.exit(main())
