"""Re-query every stored parlay market for its settlement outcome.

The collector wrote `status` and `result` once, when a market was first seen,
and never refreshed them, so 94% of stored markets still read `active`. This
pass asks the exchange what actually happened and records the answer in a
separate database, leaving the collected tape read-only.
"""
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import kalshi

log = logging.getLogger("settle")

RIG_DB = "data/rig.db"
DERIVED_DB = "data/derived.db"
BATCH = kalshi.MAX_TICKERS_PER_CALL
LOG_EVERY = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS settlements (
  ticker TEXT PRIMARY KEY,
  status TEXT,
  result TEXT,
  close_ts TEXT,
  fetched_ts TEXT
);
"""


def connect() -> sqlite3.Connection:
    parent = os.path.dirname(DERIVED_DB)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # uri=True is what lets the ATTACH below carry ?mode=ro; without it SQLite
    # treats the whole URI as a literal filename and creates a junk file.
    conn = sqlite3.connect("file:%s" % DERIVED_DB, timeout=60, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.execute("ATTACH DATABASE ? AS rig", ("file:%s?mode=ro" % RIG_DB,))
    return conn


def _resume_point(conn: sqlite3.Connection) -> str:
    """Tickers are processed in sorted order, so the highest one is the mark."""
    row = conn.execute("SELECT MAX(ticker) AS m FROM settlements").fetchone()
    return row["m"] or ""


def _next_batch(conn: sqlite3.Connection, after: str) -> List[str]:
    rows = conn.execute(
        "SELECT ticker FROM rig.markets WHERE ticker > ? "
        "ORDER BY ticker LIMIT ?", (after, BATCH)).fetchall()
    return [r["ticker"] for r in rows]


def _store(conn: sqlite3.Connection, requested: List[str],
           markets: List[Dict[str, Any]], now_iso: str) -> int:
    """Write a row for every ticker asked about.

    A ticker the exchange did not return still gets a row with a null status,
    both so the resume point always advances and so the report can say how
    many outcomes could not be confirmed, and why.
    """
    found = {m.get("ticker"): m for m in markets}
    conn.executemany(
        "INSERT OR REPLACE INTO settlements "
        "(ticker, status, result, close_ts, fetched_ts) VALUES (?,?,?,?,?)",
        [(t, found.get(t, {}).get("status"), found.get(t, {}).get("result"),
          found.get(t, {}).get("close_time"), now_iso) for t in requested])
    conn.commit()
    return len(found)


def run(conn: sqlite3.Connection) -> Dict[str, int]:
    started = time.time()
    counts = {"requested": 0, "returned": 0, "batches": 0}
    after = _resume_point(conn)
    log.info("start, resuming after ticker %r", after or "(none)")
    while True:
        batch = _next_batch(conn, after)
        if not batch:
            break
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            markets = kalshi.markets_by_tickers(batch)
        except kalshi.RateLimited:
            log.warning("rate limited, stopping cleanly at %r", after)
            break
        counts["returned"] += _store(conn, batch, markets, now_iso)
        counts["requested"] += len(batch)
        counts["batches"] += 1
        after = batch[-1]
        if counts["batches"] % LOG_EVERY == 0:
            log.info("%s, %.0fs elapsed", counts, time.time() - started)
    log.info("done in %.0fs: %s", time.time() - started, counts)
    return counts


def main() -> int:
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(filename="logs/settle.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    run(connect())
    return 0


if __name__ == "__main__":
    sys.exit(main())
