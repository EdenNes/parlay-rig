import json

import db
import collector

TRADE = {
    "trade_id": "t-1",
    "ticker": "KXMVETEST-S1-ABC",
    "created_time": "2026-07-29T20:00:00Z",
    "yes_price_dollars": "0.8000",
    "count_fp": "10.00",
    "taker_side": "yes",
}
MARKET = {
    "market": {
        "event_ticker": "KXMVETEST-S1",
        "mve_collection_ticker": "KXMVETEST-R",
        "created_time": "2026-07-29T19:59:00Z",
        "close_time": "2026-08-02T00:00:00Z",
        "volume_fp": "10.00",
        "open_interest_fp": "10.00",
        "status": "active",
        "result": "",
        "mve_selected_legs": [
            {"market_ticker": "KXNBA-LEG1", "side": "yes",
             "event_ticker": "KXNBA-E1"},
            {"market_ticker": "KXTENNIS-LEG2", "side": "no",
             "event_ticker": "KXTENNIS-E2"},
        ],
    }
}
LEG = {"market": {"yes_bid_dollars": "0.1300", "yes_ask_dollars": "0.1500"}}


def _fake_kalshi(monkeypatch, trades):
    pages = [{"trades": trades, "cursor": ""}]
    monkeypatch.setattr(collector.kalshi, "trades_page",
                        lambda min_ts, cursor=None: pages[0])
    monkeypatch.setattr(
        collector.kalshi, "market",
        lambda ticker: MARKET if ticker.startswith("KXMVE") else LEG)
    # Pin the tape window so fixtures with frozen timestamps stay fresh
    # no matter when the suite runs.
    monkeypatch.setattr(
        collector, "_min_ts",
        lambda conn: collector._epoch("2026-07-29T19:00:00Z"))


def test_cycle_stores_the_fill(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    _fake_kalshi(monkeypatch, [TRADE])
    collector.run_cycle(conn)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1


def test_cycle_snapshots_both_legs(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    _fake_kalshi(monkeypatch, [TRADE])
    collector.run_cycle(conn)
    assert conn.execute("SELECT COUNT(*) FROM leg_quotes").fetchone()[0] == 2


def test_cycle_is_idempotent(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    _fake_kalshi(monkeypatch, [TRADE])
    collector.run_cycle(conn)
    counts = collector.run_cycle(conn)
    assert counts["trades"] == 0


def test_cycle_ignores_non_mve_trades(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    other = dict(TRADE, trade_id="t-2", ticker="KXBTC15M-XYZ")
    _fake_kalshi(monkeypatch, [other])
    collector.run_cycle(conn)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_tape_paging_stops_at_min_ts(monkeypatch):
    def endless_pages(min_ts, cursor=None):
        old = dict(TRADE, ticker="KXBTC-OLD", created_time="2026-07-29T10:00:00Z")
        return {"trades": [old] * 3, "cursor": "more"}

    monkeypatch.setattr(collector.kalshi, "trades_page", endless_pages)
    fresh = collector._new_mve_trades(collector._epoch("2026-07-29T19:00:00Z"))
    assert fresh == []


def test_tape_paging_honors_hard_page_cap(monkeypatch):
    pages = []

    def counting_pages(min_ts, cursor=None):
        pages.append(1)
        fresh_enough = dict(TRADE, ticker="KXBTC-X",
                            created_time="2026-07-29T20:00:00Z")
        return {"trades": [fresh_enough] * 3, "cursor": "more"}

    monkeypatch.setattr(collector.kalshi, "trades_page", counting_pages)
    collector._new_mve_trades(collector._epoch("2026-07-29T19:00:00Z"))
    assert len(pages) == collector.MAX_TAPE_PAGES


def test_rate_limit_mid_cycle_keeps_earlier_fills(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    second = dict(TRADE, trade_id="t-2", ticker="KXMVETEST-S2-DEF",
                  created_time="2026-07-29T20:01:00Z")
    pages = [{"trades": [second, TRADE], "cursor": ""}]
    monkeypatch.setattr(collector.kalshi, "trades_page",
                        lambda min_ts, cursor=None: pages[0])
    seen = []

    def market_then_limit(ticker):
        if ticker.startswith("KXMVE"):
            seen.append(ticker)
            if len(seen) > 1:
                raise collector.kalshi.RateLimited(ticker)
            return MARKET
        return LEG

    monkeypatch.setattr(collector.kalshi, "market", market_then_limit)
    monkeypatch.setattr(
        collector, "_min_ts",
        lambda conn: collector._epoch("2026-07-29T19:00:00Z"))
    collector.run_cycle(conn)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1


def test_trades_processed_oldest_first(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    second = dict(TRADE, trade_id="t-2", ticker="KXMVETEST-S2-DEF",
                  created_time="2026-07-29T20:01:00Z")
    _fake_kalshi(monkeypatch, [second, TRADE])
    collector.run_cycle(conn)
    row = conn.execute("SELECT trade_id FROM trades ORDER BY rowid").fetchone()
    assert row["trade_id"] == "t-1"


def test_second_lock_acquisition_fails(tmp_path):
    path = str(tmp_path / "c.lock")
    held = collector._acquire_lock(path)
    assert held is not None and collector._acquire_lock(path) is None


def test_market_row_stores_legs_json(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    _fake_kalshi(monkeypatch, [TRADE])
    collector.run_cycle(conn)
    row = conn.execute("SELECT legs_json FROM markets").fetchone()
    assert len(json.loads(row["legs_json"])) == 2
