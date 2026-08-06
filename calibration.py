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
import math
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

# [0.0, 0.01, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 1.0]

def bucket_of(price: float) -> int:
    """Index of the bucket this price belongs to.

    A price equal to an edge belongs to the bucket that edge opens, and any
    price at or above the last edge belongs to the final bucket.
    """
    if (price >= 0) and (price < 0.01):
      return 0
    elif (price >= 0.01) and (price < 0.02):
      return 1
    elif (price >= 0.02) and (price < 0.05):
      return 2
    elif (price >= 0.05) and (price < 0.10):
      return 3
    elif (price >= 0.10) and (price < 0.20):
      return 4
    elif (price >= 0.20) and (price < 0.35):
      return 5
    elif (price >= 0.35) and (price < .50):
      return 6
    else:
      return 7




def wilson_interval(hits: int, n: int) -> Tuple[float, float]:
    """95% Wilson score interval for a hit rate. Returns (low, high)."""
    p = (hits/n)
    centre = ((p)+ (Z ** 2)/(2 * n)) / (1 + ((Z ** 2) /n))
    margin = (Z * math.sqrt( p * (1 - p)/n + ((Z ** 2)/(4 * (n ** 2)))) / (1 + (Z ** 2)/ n))
    return ((max(0.0, (centre - margin))), (min(1.0, (centre + margin))))



def calibrate(fills: List[Any]) -> List[Dict[str, Any]]:
    """One row per non-empty bucket, in ascending price order.

    Each row: {"bucket": int, "n": int, "hits": int, "hit_rate": float,
               "mean_price": float, "edge": float,
               "interval": (low, high)}
    where edge is the seller's, mean_price minus hit_rate.
    """
    tally = {}
    rows = []
    
    for fill in fills:
      b = bucket_of(fill["price"])
      if b not in tally:
        tally[b] = {"n":0,"hits":0,"price_sum":0.0}
      tally[b]["n"] += 1
      tally[b]["price_sum"] += fill["price"]
      if fill["result"] == "yes":
        tally[b]["hits"] += 1

  
    for b in sorted(tally):
      hit_rate = tally[b]["hits"] / tally[b]["n"]
      mean_price = tally[b]["price_sum"] / tally[b]["n"]
      edge = (mean_price - hit_rate)
      interval =  wilson_interval(tally[b]["hits"], tally[b]["n"])
      rows.append({"bucket":b, "n":tally[b]["n"], "hits":tally[b]["hits"], "hit_rate":hit_rate, "mean_price":mean_price, "edge":edge, "interval":interval})

    return rows

