"""Score sampled fills against their legs' prices at the fill's own minute.

Every rate here carries its denominator. A fill is scored only when each of
its legs has a two-sided price bar within `NEAR_SECONDS` of the fill, and the
count of fills that fail that test is reported next to the ones that pass.

Two verdicts are produced per fill. The central one prices each leg at its
mid. The conservative one prices each leg on the side that makes the claim
hardest to support: leg asks when asking whether a fill printed above the
Frechet ceiling, leg bids when asking whether it printed below the floor.
"""
import json
import os
import random
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import coverage
import scoring

NEAR_SECONDS = 120
SAMPLE = 100000

# Accepted quoters pay the standard maker rate, a quarter of the taker rate.
# Per contract that is 0.0175 * P * (1 - P), which the exchange rounds up per
# order; the per-contract form is the right unit for a per-fill edge claim.
MAKER_FEE_COEFFICIENT = 0.0175


def maker_fee(price: float) -> float:
    return MAKER_FEE_COEFFICIENT * price * (1.0 - price)


def _leg_probs(bars: List[sqlite3.Row], legs: List[Dict[str, Any]],
               side: str) -> List[float]:
    """side: 'mid', 'ask' (highest plausible leg probability) or 'bid'."""
    out = []
    for bar, leg in zip(bars, legs):
        if side == "ask":
            yes = bar["yes_ask"]
        elif side == "bid":
            yes = bar["yes_bid"]
        else:
            yes = (bar["yes_bid"] + bar["yes_ask"]) / 2.0
        out.append(scoring.leg_prob(leg.get("side", "yes"), yes))
    return out


def _worst_spread(bars: List[sqlite3.Row]) -> float:
    return max(b["yes_ask"] - b["yes_bid"] for b in bars)


def score_one(conn: sqlite3.Connection, row: sqlite3.Row) -> Optional[Dict]:
    """None when the fill cannot be scored against real leg prices."""
    legs = json.loads(row["legs_json"] or "[]")
    if not legs:
        return None
    ts = coverage._epoch(row["ts"])
    bars = [coverage._bar(conn, leg.get("market_ticker"), ts) for leg in legs]
    if any(b is None for b in bars) or not all(
            coverage._is_two_sided(b) for b in bars):
        return None
    price = row["yes_price"]
    net = price - maker_fee(price)
    central = scoring.score_fill(price, _leg_probs(bars, legs, "mid"))
    return {
        "price": price,
        "n_legs": len(legs),
        "worst_spread": _worst_spread(bars),
        "interval_width": central["ceiling"] - central["floor"],
        "central": central,
        # A seller only keeps `net`, so the edge claim must clear the ceiling
        # computed from the leg prices least favourable to that claim.
        "above_ceiling": net > scoring.frechet_ceiling(
            _leg_probs(bars, legs, "ask")),
        "below_floor": price < scoring.frechet_floor(
            _leg_probs(bars, legs, "bid")),
    }


def gather(conn: sqlite3.Connection, n: int = SAMPLE) -> Dict[str, Any]:
    rows = coverage._sample(conn, n)
    scored = []
    for row in rows:
        result = score_one(conn, row)
        if result is not None:
            scored.append(result)
    return {"sampled": len(rows), "scored": scored}


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def summarise(data: Dict[str, Any]) -> Dict[str, Any]:
    scored = data["scored"]
    n = len(scored)
    above = sum(1 for s in scored if s["above_ceiling"])
    below = sum(1 for s in scored if s["below_floor"])
    incoherent_central = sum(1 for s in scored if not s["central"]["coherent"])
    return {
        "sampled": data["sampled"],
        "scored": n,
        "scorable_pct": _pct(n, data["sampled"]),
        "above_ceiling": above,
        "above_ceiling_pct": _pct(above, n),
        "below_floor": below,
        "below_floor_pct": _pct(below, n),
        "incoherent_central": incoherent_central,
        "incoherent_central_pct": _pct(incoherent_central, n),
        "median_legs": _median([s["n_legs"] for s in scored]),
        "median_worst_spread": round(
            _median([s["worst_spread"] for s in scored]), 4),
        "median_interval_width": round(
            _median([s["interval_width"] for s in scored]), 4),
    }


def build(conn: sqlite3.Connection, n: int = SAMPLE) -> str:
    s = summarise(gather(conn, n))
    return "\n".join([
        "# Parlay coherence report",
        "",
        "Generated %s UTC." % datetime.now(timezone.utc).isoformat(),
        "",
        "## Sample",
        "",
        "- fills drawn: %d" % s["sampled"],
        "- fills scorable against two-sided leg prices within %ds: %d (%.2f%%)"
        % (NEAR_SECONDS, s["scored"], s["scorable_pct"]),
        "- median legs per scored fill: %s" % s["median_legs"],
        "- median worst leg spread: %.4f" % s["median_worst_spread"],
        "- median Frechet interval width: %.4f" % s["median_interval_width"],
        "",
        "## Coherence",
        "",
        "%d incoherent of %d scored (%.2f%%), pricing legs at their mid."
        % (s["incoherent_central"], s["scored"], s["incoherent_central_pct"]),
        "",
        "Netting the maker fee and pricing each leg on the side least",
        "favourable to the claim:",
        "",
        "- above the ceiling: %d of %d (%.2f%%)"
        % (s["above_ceiling"], s["scored"], s["above_ceiling_pct"]),
        "- below the floor: %d of %d (%.2f%%)"
        % (s["below_floor"], s["scored"], s["below_floor_pct"]),
        "",
        "## What this does not say",
        "",
        "- These are prints, not the RFQ auction. A price outside the interval",
        "  was not necessarily available to quote against.",
        "- The unscorable remainder skews toward illiquid legs, which is where",
        "  mispricing is most likely, so these rates may understate the total.",
        "",
    ])


def main() -> int:
    import settle
    n = int(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE
    text = build(settle.connect(), n)
    os.makedirs("reports", exist_ok=True)
    path = "reports/%s.md" % datetime.now(timezone.utc).date().isoformat()
    with open(path, "w") as handle:
        handle.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
