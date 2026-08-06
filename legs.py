"""Work out which window of history each leg market needs.

The tape stores every parlay's legs but nothing about the legs themselves.
A leg only needs price history covering the parlays that reference it, so
this pass reads `legs_json` once and records, per leg, the earliest parlay
creation and the latest parlay close it appears in.
"""
import json
import logging
import os
import sqlite3
import sys
import time
from typing import Dict, Optional, Tuple

import kalshi

log = logging.getLogger("legs")

CHUNK = 20000
LOG_EVERY = 200000

SCHEMA = """
CREATE TABLE IF NOT EXISTS leg_windows (
  leg_ticker TEXT PRIMARY KEY,
  start_ts TEXT,
  end_ts TEXT,
  n_parlays INTEGER
);
"""


def _minmax(current: Tuple[Optional[str], Optional[str], int],
            created: Optional[str], close: Optional[str]):
    """Widen a leg's window. Timestamps are RFC3339, so string order is time
    order, which keeps this a comparison rather than a parse per row."""
    lo, hi, n = current
    if created and (lo is None or created < lo):
        lo = created
    if close and (hi is None or close > hi):
        hi = close
    return (lo, hi, n + 1)


def build(conn: sqlite3.Connection) -> Dict[str, int]:
    started = time.time()
    conn.executescript(SCHEMA)
    windows = {}  # type: Dict[str, Tuple[Optional[str], Optional[str], int]]
    seen = 0
    cur = conn.execute(
        "SELECT legs_json, created_ts, close_ts FROM rig.markets")
    while True:
        rows = cur.fetchmany(CHUNK)
        if not rows:
            break
        for row in rows:
            seen += 1
            try:
                legs = json.loads(row["legs_json"] or "[]")
            except ValueError:
                continue
            for leg in legs:
                ticker = leg.get("market_ticker")
                if not ticker:
                    continue
                windows[ticker] = _minmax(
                    windows.get(ticker, (None, None, 0)),
                    row["created_ts"], row["close_ts"])
        if seen % LOG_EVERY == 0:
            log.info("%d markets read, %d legs, %.0fs",
                     seen, len(windows), time.time() - started)
    conn.executemany(
        "INSERT OR REPLACE INTO leg_windows "
        "(leg_ticker, start_ts, end_ts, n_parlays) VALUES (?,?,?,?)",
        [(k, v[0], v[1], v[2]) for k, v in windows.items()])
    conn.commit()
    counts = {"markets": seen, "legs": len(windows)}
    log.info("done in %.0fs: %s", time.time() - started, counts)
    return counts


def tape_bounds(conn: sqlite3.Connection) -> Tuple[str, str]:
    row = conn.execute("SELECT MIN(ts) AS lo, MAX(ts) AS hi "
                       "FROM rig.trades").fetchone()
    return row["lo"], row["hi"]


def refine(conn: sqlite3.Connection) -> Dict[str, int]:
    """Narrow each window to when the leg could actually have been priced.

    A window derived from parlay lifetimes alone runs to 40 days for popular
    legs, because parlays referencing them close far in the future. A leg has
    no prices before its own market opened, and no fill sits outside the
    collected tape, so the intersection of those three is the real window.
    """
    started = time.time()
    lo_tape, hi_tape = tape_bounds(conn)
    log.info("tape spans %s to %s", lo_tape, hi_tape)
    tickers = [r["leg_ticker"] for r in
               conn.execute("SELECT leg_ticker FROM leg_windows")]
    narrowed = 0
    for i in range(0, len(tickers), kalshi.MAX_TICKERS_PER_CALL):
        batch = tickers[i:i + kalshi.MAX_TICKERS_PER_CALL]
        for m in kalshi.markets_by_tickers(batch):
            conn.execute(
                "UPDATE leg_windows SET start_ts = MAX(start_ts, ?, ?), "
                "end_ts = MIN(end_ts, ?, ?) WHERE leg_ticker = ?",
                (m.get("open_time") or lo_tape, lo_tape,
                 m.get("close_time") or hi_tape, hi_tape, m.get("ticker")))
            narrowed += 1
        conn.commit()
        if i and i % 10000 == 0:
            log.info("%d legs narrowed, %.0fs", narrowed, time.time() - started)
    counts = {"legs": len(tickers), "narrowed": narrowed}
    log.info("refine done in %.0fs: %s", time.time() - started, counts)
    return counts


def main() -> int:
    import settle
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(filename="logs/legs.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    conn = settle.connect()
    if "--refine" not in sys.argv:
        build(conn)
    refine(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
