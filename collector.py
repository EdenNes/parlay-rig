import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

import db
import kalshi

log = logging.getLogger("rig")

MVE_PREFIX = "KXMVE"
# First-ever cycle covers 15 minutes; history depth is backfill's job.
# Steady-state cycles resume from the newest stored trade regardless.
BOOTSTRAP_SECONDS = 900
OVERLAP_SECONDS = 60
MAX_TAPE_PAGES = 300
COMMIT_EVERY = 500


def _epoch(rfc3339: str) -> int:
    return int(datetime.fromisoformat(
        rfc3339.replace("Z", "+00:00")).timestamp())


def _min_ts(conn) -> int:
    row = conn.execute("SELECT MAX(ts) AS m FROM trades").fetchone()
    if row["m"] is None:
        return int(time.time()) - BOOTSTRAP_SECONDS
    return _epoch(row["m"]) - OVERLAP_SECONDS


def _new_mve_trades(min_ts: int) -> List[Dict[str, Any]]:
    """Page the exchange-wide tape newest-first, bounded client-side.

    The server's cursor can walk arbitrarily far into history, so we stop
    ourselves: once a batch's oldest trade predates min_ts, or after
    MAX_TAPE_PAGES pages, whichever comes first.
    """
    out = []  # type: List[Dict[str, Any]]
    cursor = None
    for page_n in range(1, MAX_TAPE_PAGES + 1):
        page = kalshi.trades_page(min_ts, cursor)
        batch = page.get("trades", [])
        out.extend(t for t in batch if t["ticker"].startswith(MVE_PREFIX)
                   and _epoch(t["created_time"]) >= min_ts)
        cursor = page.get("cursor")
        if page_n % 25 == 0:
            log.info("tape page %d, %d mve trades so far", page_n, len(out))
        if not cursor or not batch:
            return out
        if _epoch(batch[-1]["created_time"]) < min_ts:
            return out
    log.warning("hit MAX_TAPE_PAGES=%d, returning partial window",
                MAX_TAPE_PAGES)
    return out


def _num(value: Any) -> float:
    return float(value or 0)


def _ensure_market(conn, ticker: str, now_iso: str) -> Tuple[List[str], bool]:
    """Return (leg market tickers, was_newly_inserted)."""
    row = conn.execute(
        "SELECT legs_json FROM markets WHERE ticker = ?", (ticker,)).fetchone()
    if row is not None:
        legs = json.loads(row["legs_json"] or "[]")
        return [leg["market_ticker"] for leg in legs], False
    m = kalshi.market(ticker).get("market", {})
    legs = m.get("mve_selected_legs") or []
    conn.execute(
        "INSERT OR REPLACE INTO markets (ticker, series, event_ticker, "
        "collection, legs_json, created_ts, close_ts, volume_fp, oi_fp, "
        "status, result, last_seen_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ticker, ticker.split("-")[0], m.get("event_ticker"),
         m.get("mve_collection_ticker"), json.dumps(legs),
         m.get("created_time"), m.get("close_time"),
         _num(m.get("volume_fp")), _num(m.get("open_interest_fp")),
         m.get("status"), m.get("result"), now_iso))
    return [leg["market_ticker"] for leg in legs], True


def _snapshot_legs(conn, leg_tickers: Set[str], now_iso: str) -> int:
    n = 0
    for leg in sorted(leg_tickers):
        m = kalshi.market(leg).get("market", {})
        conn.execute(
            "INSERT INTO leg_quotes (leg_ticker, ts, yes_bid, yes_ask) "
            "VALUES (?,?,?,?)",
            (leg, now_iso, _num(m.get("yes_bid_dollars")),
             _num(m.get("yes_ask_dollars"))))
        n += 1
    return n


def _census(conn, now_iso: str) -> None:
    rows = conn.execute(
        "SELECT m.series AS series, COUNT(DISTINCT m.ticker) AS n, "
        "COALESCE(SUM(t.count_fp), 0) AS vol "
        "FROM markets m LEFT JOIN trades t ON t.ticker = m.ticker "
        "GROUP BY m.series").fetchall()
    for r in rows:
        conn.execute(
            "INSERT INTO census (ts, series, n_markets, total_volume_fp) "
            "VALUES (?,?,?,?)", (now_iso, r["series"], r["n"], r["vol"]))


def run_cycle(conn) -> Dict[str, int]:
    started = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    counts = {"trades": 0, "markets": 0, "legs": 0}
    min_ts = _min_ts(conn)
    log.info("cycle start, tape window from %d (%.0fs back)",
             min_ts, time.time() - min_ts)
    try:
        fresh = _new_mve_trades(min_ts)
    except kalshi.RateLimited:
        log.warning("rate limited, ending cycle early")
        return counts
    # Oldest first: the resume point is max(stored ts), so it must only move
    # past trades that were fully processed. Newest-first + mid-cycle death
    # would strand older trades behind the resume point forever.
    fresh.sort(key=lambda t: t["created_time"])
    legs_to_quote = set()  # type: Set[str]
    try:
        for i, t in enumerate(fresh, 1):
            leg_tickers, was_new = _ensure_market(conn, t["ticker"], now_iso)
            counts["markets"] += int(was_new)
            cur = conn.execute(
                "INSERT OR IGNORE INTO trades (trade_id, ticker, ts, yes_price, "
                "count_fp, taker_side) VALUES (?,?,?,?,?,?)",
                (t["trade_id"], t["ticker"], t["created_time"],
                 _num(t.get("yes_price_dollars")), _num(t.get("count_fp")),
                 t.get("taker_side")))
            if cur.rowcount == 1:
                counts["trades"] += 1
                legs_to_quote.update(leg_tickers)
            if i % COMMIT_EVERY == 0:
                conn.commit()
                log.info("progress: %d/%d trades processed, %s",
                         i, len(fresh), counts)
        counts["legs"] = _snapshot_legs(conn, legs_to_quote, now_iso)
    except kalshi.RateLimited:
        log.warning("rate limited mid-cycle, committing partial progress")
    _census(conn, now_iso)
    conn.commit()
    log.info("cycle done in %.1fs: %s", time.time() - started, counts)
    return counts


def _acquire_lock(path: str = "data/collector.lock"):
    """Advisory single-instance lock; the OS frees it if the process dies."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> int:
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(filename="logs/collector.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    lock = _acquire_lock()
    if lock is None:
        log.info("previous cycle still running, exiting")
        return 0
    conn = db.connect()
    run_cycle(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
