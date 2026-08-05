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


def main() -> int:
    import settle
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(filename="logs/legs.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    build(settle.connect())
    return 0


if __name__ == "__main__":
    sys.exit(main())
