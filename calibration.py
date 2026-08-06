"""Does the price predict the outcome? (EDEN WRITES THIS)

Coherence asks whether a price is internally possible given its legs.
Calibration asks the other question: across many settled fills, does a parlay
that printed at 12c actually hit about 12% of the time? If it hits less often,
the seller of those contracts earned the difference.

Fill in the three functions below until `tests/test_calibration.py` passes.
Claude wrote the tests as the contract and does not write these bodies.

Notes before you start:

* Work from the SELLER's side, since that is the market maker's position.
  Edge per contract = price - realized hit rate. Positive means the seller
  collected more than the outcome cost.
* Every fill counts, hits and misses. Filtering on the outcome is the bias
  that makes the whole exercise meaningless.
* A hit rate needs an interval, not just a point. Use the Wilson score
  interval rather than the normal approximation: at a 2c price with 30
  settled fills, the normal approximation happily produces a negative lower
  bound, which is not a probability. The formula, for hits h out of n, with
  p = h / n and z = 1.96:

      centre = (p + z^2 / (2n)) / (1 + z^2 / n)
      margin = z * sqrt(p(1 - p)/n + z^2/(4n^2)) / (1 + z^2 / n)

  and the interval is centre +/- margin.
"""
import random
import sqlite3
from typing import Any, Dict, List, Tuple

# Prices below 20c carry most parlay volume, so the buckets are finer there.
BUCKET_EDGES = [0.0, 0.01, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 1.0]
Z = 1.96


def settled_fills(conn: sqlite3.Connection, n: int) -> List[sqlite3.Row]:
    """Sample fills whose parlay has a confirmed yes/no outcome.

    Provided for you: joining the tape to the settlement table is plumbing,
    not the math under test.
    """
    top = conn.execute("SELECT MAX(rowid) FROM rig.trades").fetchone()[0] or 0
    ids = {random.randint(1, top) for _ in range(n * 3)}
    marks = ",".join("?" * len(ids))
    return conn.execute(
        "SELECT t.yes_price AS price, s.result AS result "
        "FROM rig.trades t JOIN settlements s ON s.ticker = t.ticker "
        "WHERE t.rowid IN (%s) AND s.result IN ('yes','no') LIMIT ?" % marks,
        list(ids) + [n]).fetchall()


def bucket_of(price: float) -> int:
    """Index of the bucket this price belongs to.

    A price equal to an edge belongs to the bucket that edge opens, and any
    price at or above the last edge belongs to the final bucket.
    """
    raise NotImplementedError("Eden writes this")


def wilson_interval(hits: int, n: int) -> Tuple[float, float]:
    """95% Wilson score interval for a hit rate. Returns (low, high)."""
    raise NotImplementedError("Eden writes this")


def calibrate(fills: List[Any]) -> List[Dict[str, Any]]:
    """One row per non-empty bucket, in ascending price order.

    Each row: {"bucket": int, "n": int, "hits": int, "hit_rate": float,
               "mean_price": float, "edge": float,
               "interval": (low, high)}
    where edge is the seller's, mean_price minus hit_rate.
    """
    raise NotImplementedError("Eden writes this")
