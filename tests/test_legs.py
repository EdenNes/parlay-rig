import json
import sqlite3

import pytest

import legs


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("ATTACH DATABASE ':memory:' AS rig")
    c.execute("CREATE TABLE rig.markets (ticker TEXT, legs_json TEXT, "
              "created_ts TEXT, close_ts TEXT)")
    return c


def add(conn, ticker, leg_tickers, created, close):
    conn.execute(
        "INSERT INTO rig.markets VALUES (?,?,?,?)",
        (ticker, json.dumps([{"market_ticker": t, "side": "yes"}
                             for t in leg_tickers]), created, close))


def windows(conn):
    return {r["leg_ticker"]: r for r in conn.execute(
        "SELECT * FROM leg_windows")}


def test_a_leg_takes_the_window_of_its_only_parlay(conn):
    add(conn, "P1", ["L1"], "2026-08-01T00:00:00Z", "2026-08-01T06:00:00Z")
    legs.build(conn)
    row = windows(conn)["L1"]
    assert (row["start_ts"], row["end_ts"]) == (
        "2026-08-01T00:00:00Z", "2026-08-01T06:00:00Z")


def test_a_shared_leg_spans_the_earliest_start_and_latest_close(conn):
    add(conn, "P1", ["L1"], "2026-08-02T00:00:00Z", "2026-08-02T06:00:00Z")
    add(conn, "P2", ["L1"], "2026-08-01T00:00:00Z", "2026-08-03T06:00:00Z")
    legs.build(conn)
    row = windows(conn)["L1"]
    assert (row["start_ts"], row["end_ts"]) == (
        "2026-08-01T00:00:00Z", "2026-08-03T06:00:00Z")


def test_every_leg_of_a_parlay_is_recorded(conn):
    add(conn, "P1", ["L1", "L2", "L3"], "2026-08-01T00:00:00Z", "T")
    legs.build(conn)
    assert sorted(windows(conn)) == ["L1", "L2", "L3"]


def test_parlay_count_per_leg_is_kept(conn):
    add(conn, "P1", ["L1"], "A", "B")
    add(conn, "P2", ["L1"], "A", "B")
    legs.build(conn)
    assert windows(conn)["L1"]["n_parlays"] == 2


def test_a_market_with_unreadable_legs_does_not_stop_the_pass(conn):
    conn.execute("INSERT INTO rig.markets VALUES ('P1','{bad',  'A', 'B')")
    add(conn, "P2", ["L1"], "A", "B")
    assert legs.build(conn)["legs"] == 1


def test_a_market_with_no_legs_contributes_nothing(conn):
    conn.execute("INSERT INTO rig.markets VALUES ('P1', NULL, 'A', 'B')")
    assert legs.build(conn)["legs"] == 0


def test_rebuilding_replaces_rather_than_duplicates(conn):
    add(conn, "P1", ["L1"], "A", "B")
    legs.build(conn)
    legs.build(conn)
    assert conn.execute("SELECT COUNT(*) FROM leg_windows").fetchone()[0] == 1
