import sqlite3

import pytest

import legs


@pytest.fixture
def conn(monkeypatch):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(legs.SCHEMA)
    c.execute("ATTACH DATABASE ':memory:' AS rig")
    c.execute("CREATE TABLE rig.trades (ts TEXT)")
    c.executemany("INSERT INTO rig.trades VALUES (?)",
                  [("2026-07-30T00:00:00Z",), ("2026-08-05T00:00:00Z",)])
    c.execute("INSERT INTO leg_windows VALUES ('L1', "
              "'2026-07-01T00:00:00Z', '2026-09-01T00:00:00Z', 5)")
    return c


def market(open_time, close_time):
    return [{"ticker": "L1", "open_time": open_time,
             "close_time": close_time}]


def window(conn):
    r = conn.execute("SELECT * FROM leg_windows WHERE leg_ticker='L1'"
                     ).fetchone()
    return r["start_ts"], r["end_ts"]


def test_tape_bounds_are_the_first_and_last_trade(conn):
    assert legs.tape_bounds(conn) == ("2026-07-30T00:00:00Z",
                                      "2026-08-05T00:00:00Z")


def test_a_window_wider_than_the_tape_is_clipped_to_the_tape(conn,
                                                             monkeypatch):
    monkeypatch.setattr(legs, "kalshi", _fake(market("2026-06-01T00:00:00Z",
                                                     "2026-10-01T00:00:00Z")))
    legs.refine(conn)
    assert window(conn) == ("2026-07-30T00:00:00Z", "2026-08-05T00:00:00Z")


def test_a_leg_opening_late_starts_when_it_opened(conn, monkeypatch):
    monkeypatch.setattr(legs, "kalshi", _fake(market("2026-08-02T00:00:00Z",
                                                     "2026-10-01T00:00:00Z")))
    legs.refine(conn)
    assert window(conn)[0] == "2026-08-02T00:00:00Z"


def test_a_leg_closing_early_ends_when_it_closed(conn, monkeypatch):
    monkeypatch.setattr(legs, "kalshi", _fake(market("2026-06-01T00:00:00Z",
                                                     "2026-08-01T00:00:00Z")))
    legs.refine(conn)
    assert window(conn)[1] == "2026-08-01T00:00:00Z"


def test_a_leg_with_no_times_falls_back_to_the_tape(conn, monkeypatch):
    monkeypatch.setattr(legs, "kalshi", _fake(market(None, None)))
    legs.refine(conn)
    assert window(conn) == ("2026-07-30T00:00:00Z", "2026-08-05T00:00:00Z")


def _fake(markets):
    class Fake:
        MAX_TICKERS_PER_CALL = 100

        @staticmethod
        def markets_by_tickers(tickers):
            return markets

    return Fake
