import json
import sqlite3

import pytest

import backfill
import coverage

FILL_TS = "2026-08-01T12:00:00Z"
FILL_EPOCH = 1785585600


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(backfill.SCHEMA)
    c.execute("ATTACH DATABASE ':memory:' AS rig")
    c.execute("CREATE TABLE rig.trades (trade_id TEXT, ticker TEXT, ts TEXT, "
              "yes_price REAL)")
    c.execute("CREATE TABLE rig.markets (ticker TEXT, legs_json TEXT)")
    return c


def add_fill(conn, legs):
    conn.execute("INSERT INTO rig.trades VALUES ('t1','P1',?,0.2)",
                 (FILL_TS,))
    conn.execute("INSERT INTO rig.markets VALUES ('P1',?)",
                 (json.dumps([{"market_ticker": leg, "side": "yes"}
                              for leg in legs]),))


def add_bar(conn, leg, offset, bid=0.4, ask=0.42):
    conn.execute("INSERT INTO leg_candles VALUES (?,?,?,?)",
                 (leg, FILL_EPOCH + offset, bid, ask))


def mark_fetched(conn, leg):
    conn.execute("INSERT OR REPLACE INTO leg_backfilled VALUES (?,1,1,NULL,'t')",
                 (leg,))


def verdict(conn):
    row = conn.execute("SELECT t.trade_id, t.ticker, t.ts, t.yes_price, "
                       "m.legs_json FROM rig.trades t JOIN rig.markets m "
                       "ON m.ticker = t.ticker").fetchone()
    return coverage.classify(conn, row)


def test_a_fill_whose_legs_all_have_nearby_two_sided_bars_is_scorable(conn):
    add_fill(conn, ["L1", "L2"])
    add_bar(conn, "L1", 0)
    add_bar(conn, "L2", -30)
    assert verdict(conn) == "scorable"


def test_a_leg_not_yet_backfilled_is_reported_as_progress_not_quality(conn):
    add_fill(conn, ["L1", "L2"])
    add_bar(conn, "L1", 0)
    assert verdict(conn) == "leg_not_fetched"


def test_a_fetched_leg_with_no_nearby_bar_is_a_data_gap(conn):
    add_fill(conn, ["L1"])
    add_bar(conn, "L1", coverage.NEAR_SECONDS + 60)
    mark_fetched(conn, "L1")
    assert verdict(conn) == "no_bar_near_fill"


def test_an_empty_book_quoted_zero_to_one_is_not_scorable(conn):
    add_fill(conn, ["L1"])
    add_bar(conn, "L1", 0, bid=0.0, ask=1.0)
    assert verdict(conn) == "one_sided_book"


def test_a_parlay_with_no_stored_legs_is_reported_separately(conn):
    add_fill(conn, [])
    assert verdict(conn) == "no_legs"


def test_the_nearest_bar_is_the_one_used(conn):
    add_fill(conn, ["L1"])
    add_bar(conn, "L1", -90, bid=0.0, ask=1.0)
    add_bar(conn, "L1", 10)
    assert verdict(conn) == "scorable"


def test_run_reports_a_percentage_with_its_denominator(conn):
    add_fill(conn, ["L1"])
    add_bar(conn, "L1", 0)
    result = coverage.run(conn, 10)
    assert result["sampled"] == 1
    assert result["scorable_pct"] == 100.0
