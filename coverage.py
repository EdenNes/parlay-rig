"""Measure how many fills can be scored against real leg prices.

Every rate this rig reports needs a denominator. A fill is only scorable if
each of its legs has a price bar near the fill's own timestamp and that bar
shows a genuine two-sided book. This samples the tape and counts which fills
clear that bar, and why the rest do not.
"""
import json
import random
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

SAMPLE = 20000
NEAR_SECONDS = 120


def _epoch(rfc3339: str) -> int:
    return int(datetime.fromisoformat(
        rfc3339.replace("Z", "+00:00")).timestamp())


def _sample(conn: sqlite3.Connection, n: int) -> List[sqlite3.Row]:
    """Random rowids rather than ORDER BY RANDOM(), which sorts the whole
    3.7M-row tape before taking its first n."""
    top = conn.execute("SELECT MAX(rowid) FROM rig.trades").fetchone()[0] or 0
    ids = {random.randint(1, top) for _ in range(n * 2)}
    marks = ",".join("?" * len(ids))
    return conn.execute(
        "SELECT t.trade_id, t.ticker, t.ts, t.yes_price, m.legs_json "
        "FROM rig.trades t JOIN rig.markets m ON m.ticker = t.ticker "
        "WHERE t.rowid IN (%s) LIMIT ?" % marks,
        list(ids) + [n]).fetchall()


def _bar(conn: sqlite3.Connection, leg: str, ts: int) -> Optional[sqlite3.Row]:
    """Nearest bar to the fill, within NEAR_SECONDS on either side."""
    return conn.execute(
        "SELECT yes_bid, yes_ask, ts FROM leg_candles WHERE leg_ticker = ? "
        "AND ts BETWEEN ? AND ? ORDER BY ABS(ts - ?) LIMIT 1",
        (leg, ts - NEAR_SECONDS, ts + NEAR_SECONDS, ts)).fetchone()


def _all_fetched(conn: sqlite3.Connection, tickers: List[str]) -> bool:
    marks = ",".join("?" * len(tickers))
    n = conn.execute("SELECT COUNT(*) FROM leg_backfilled WHERE leg_ticker "
                     "IN (%s)" % marks, tickers).fetchone()[0]
    return n == len(tickers)


def _is_two_sided(bar: sqlite3.Row) -> bool:
    return bar["yes_bid"] > 0 and bar["yes_ask"] < 1


def classify(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    """One verdict per fill: why it is or is not scorable."""
    legs = json.loads(row["legs_json"] or "[]")
    if not legs:
        return "no_legs"
    ts = _epoch(row["ts"])
    tickers = [leg.get("market_ticker") for leg in legs]
    bars = [_bar(conn, t, ts) for t in tickers]
    if any(b is None for b in bars):
        # While the backfill is still running, an absent bar usually means the
        # leg has not been fetched yet. That is a progress fact, not a data
        # quality fact, and mixing the two understates real coverage.
        missing = [t for t, b in zip(tickers, bars) if b is None]
        return ("leg_not_fetched" if not _all_fetched(conn, missing)
                else "no_bar_near_fill")
    if not all(_is_two_sided(b) for b in bars):
        return "one_sided_book"
    return "scorable"


def run(conn: sqlite3.Connection, n: int = SAMPLE) -> Dict[str, Any]:
    rows = _sample(conn, n)
    counts = {}  # type: Dict[str, int]
    for row in rows:
        verdict = classify(conn, row)
        counts[verdict] = counts.get(verdict, 0) + 1
    total = len(rows) or 1
    return {"sampled": total, "counts": counts,
            "scorable_pct": round(100.0 * counts.get("scorable", 0) / total, 1)}


def main() -> int:
    import settle
    n = int(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE
    result = run(settle.connect(), n)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
