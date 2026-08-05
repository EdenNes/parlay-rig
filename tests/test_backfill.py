import sqlite3

import pytest

import backfill
import legs


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(legs.SCHEMA)
    c.executescript(backfill.SCHEMA)
    c.execute("INSERT INTO leg_windows VALUES ('KXA-1', "
              "'2026-08-01T00:00:00+00:00', '2026-08-01T06:00:00+00:00', 3)")
    return c


def bar(ts, bid, ask):
    return {"end_period_ts": ts,
            "yes_bid": {"close_dollars": bid},
            "yes_ask": {"close_dollars": ask}}


def test_a_short_window_is_a_single_request():
    assert len(backfill._chunks(0, 3600)) == 1


def test_a_multi_day_window_is_split_at_the_one_day_cap():
    assert len(backfill._chunks(0, 3 * 86400)) == 4


def test_chunks_are_padded_so_edge_fills_still_have_a_bar():
    assert backfill._chunks(10000, 10001)[0][0] == 10000 - backfill.PAD


def test_store_writes_the_closing_bid_and_ask_of_each_bar(conn):
    backfill._store(conn, "KXA-1", [bar(60, "0.61", "0.63")])
    row = conn.execute("SELECT * FROM leg_candles").fetchone()
    assert (row["yes_bid"], row["yes_ask"]) == (0.61, 0.63)


def test_store_skips_a_bar_with_no_timestamp(conn):
    backfill._store(conn, "KXA-1", [{"yes_bid": {"close_dollars": "0.5"}}])
    assert conn.execute("SELECT COUNT(*) FROM leg_candles").fetchone()[0] == 0


def test_refetching_the_same_minute_does_not_duplicate_it(conn):
    backfill._store(conn, "KXA-1", [bar(60, "0.61", "0.63")])
    backfill._store(conn, "KXA-1", [bar(60, "0.70", "0.72")])
    rows = conn.execute("SELECT yes_bid FROM leg_candles").fetchall()
    assert [r["yes_bid"] for r in rows] == [0.70]


def test_run_fetches_every_pending_leg(conn, monkeypatch):
    monkeypatch.setattr(backfill, "_bars",
                        lambda leg, lo, hi: [bar(lo, "0.4", "0.5")])
    counts = backfill.run(conn)
    assert counts["legs"] == 1
    assert counts["bars"] == 1


def test_a_completed_leg_is_not_fetched_again(conn, monkeypatch):
    monkeypatch.setattr(backfill, "_bars", lambda leg, lo, hi: [])
    backfill.run(conn)
    assert backfill._pending(conn) == []


def test_a_leg_whose_window_is_unknown_is_skipped(conn):
    conn.execute("INSERT INTO leg_windows VALUES ('KXB-1', NULL, NULL, 1)")
    assert [p[0] for p in backfill._pending(conn)] == ["KXA-1"]


def test_a_failing_leg_records_its_error_instead_of_vanishing(conn,
                                                              monkeypatch):
    def boom(leg, lo, hi):
        raise OSError("connection reset")

    monkeypatch.setattr(backfill, "_bars", boom)
    backfill.run(conn)
    row = conn.execute("SELECT error FROM leg_backfilled").fetchone()
    assert "connection reset" in row["error"]


def test_rate_limiting_stops_the_run_without_marking_the_leg_done(conn,
                                                                  monkeypatch):
    def limited(leg, lo, hi):
        raise backfill.kalshi.RateLimited("/candlesticks")

    monkeypatch.setattr(backfill, "_bars", limited)
    assert backfill.run(conn)["legs"] == 0
    assert len(backfill._pending(conn)) == 1
