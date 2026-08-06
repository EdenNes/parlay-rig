import json
import sqlite3

import pytest

import backfill
import report

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


def seed(conn, price, legs, bars):
    conn.execute("INSERT INTO rig.trades VALUES ('t1','P1',?,?)",
                 (FILL_TS, price))
    conn.execute("INSERT INTO rig.markets VALUES ('P1',?)",
                 (json.dumps(legs),))
    for leg, (bid, ask) in zip(legs, bars):
        conn.execute("INSERT INTO leg_candles VALUES (?,?,?,?)",
                     (leg["market_ticker"], FILL_EPOCH, bid, ask))


def only_row(conn):
    return conn.execute(
        "SELECT t.trade_id, t.ticker, t.ts, t.yes_price, m.legs_json "
        "FROM rig.trades t JOIN rig.markets m ON m.ticker = t.ticker"
    ).fetchone()


def yes(*tickers):
    return [{"market_ticker": t, "side": "yes"} for t in tickers]


def test_the_study_case_prices_above_its_ceiling(conn):
    seed(conn, 0.80, yes("L1", "L2"), [(0.80, 0.84), (0.12, 0.16)])
    result = report.score_one(conn, only_row(conn))
    assert result["above_ceiling"] is True
    assert result["central"]["coherent"] is False


def test_a_fill_under_the_ceiling_is_coherent(conn):
    seed(conn, 0.10, yes("L1", "L2"), [(0.80, 0.84), (0.12, 0.16)])
    result = report.score_one(conn, only_row(conn))
    assert result["above_ceiling"] is False
    assert result["central"]["coherent"] is True


def test_a_no_side_leg_is_scored_on_the_complement(conn):
    legs = [{"market_ticker": "L1", "side": "no"}]
    seed(conn, 0.50, legs, [(0.80, 0.80)])
    result = report.score_one(conn, only_row(conn))
    assert result["central"]["ceiling"] == pytest.approx(0.20)


def test_a_fill_with_a_missing_leg_bar_is_not_scored(conn):
    seed(conn, 0.80, yes("L1", "L2"), [(0.80, 0.84)])
    assert report.score_one(conn, only_row(conn)) is None


def test_a_one_sided_book_is_not_scored(conn):
    seed(conn, 0.80, yes("L1"), [(0.0, 1.0)])
    assert report.score_one(conn, only_row(conn)) is None


def test_the_maker_fee_is_a_quarter_of_the_taker_rate():
    assert report.maker_fee(0.5) == pytest.approx(0.0175 * 0.25)


def test_the_fee_makes_a_marginal_edge_claim_fail(conn):
    # Fill sits a hair above the ask-side ceiling; the fee eats the margin.
    seed(conn, 0.2005, yes("L1"), [(0.19, 0.20)])
    assert report.score_one(conn, only_row(conn))["above_ceiling"] is False


def test_a_fill_below_the_bid_side_floor_is_flagged(conn):
    seed(conn, 0.50, yes("L1", "L2"), [(0.90, 0.92), (0.80, 0.82)])
    assert report.score_one(conn, only_row(conn))["below_floor"] is True


def test_summarise_reports_counts_with_denominators(conn):
    seed(conn, 0.80, yes("L1", "L2"), [(0.80, 0.84), (0.12, 0.16)])
    s = report.summarise(report.gather(conn, 10))
    assert s["scored"] == 1
    assert s["above_ceiling"] == 1
    assert s["above_ceiling_pct"] == 100.0


def test_build_states_the_denominator_in_words(conn):
    seed(conn, 0.80, yes("L1", "L2"), [(0.80, 0.84), (0.12, 0.16)])
    assert "1 incoherent of 1 scored" in report.build(conn, 10)


def test_build_keeps_the_limitations_section(conn):
    seed(conn, 0.80, yes("L1"), [(0.80, 0.84)])
    assert "What this does not say" in report.build(conn, 10)
