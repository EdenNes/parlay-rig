import sqlite3

import pytest

import settle


@pytest.fixture
def conn(tmp_path, monkeypatch):
    rig = tmp_path / "rig.db"
    src = sqlite3.connect(str(rig))
    src.execute("CREATE TABLE markets (ticker TEXT PRIMARY KEY, status TEXT)")
    src.executemany("INSERT INTO markets (ticker) VALUES (?)",
                    [("A",), ("B",), ("C",)])
    src.commit()
    src.close()
    monkeypatch.setattr(settle, "RIG_DB", str(rig))
    monkeypatch.setattr(settle, "DERIVED_DB", str(tmp_path / "derived.db"))
    return settle.connect()


def test_resume_point_is_empty_on_a_fresh_database(conn):
    assert settle._resume_point(conn) == ""


def test_next_batch_reads_tickers_from_the_attached_tape(conn, monkeypatch):
    monkeypatch.setattr(settle, "BATCH", 2)
    assert settle._next_batch(conn, "") == ["A", "B"]


def test_next_batch_skips_past_the_resume_point(conn):
    assert settle._next_batch(conn, "A") == ["B", "C"]


def test_store_records_the_outcome_the_exchange_returned(conn):
    settle._store(conn, ["A"], [{"ticker": "A", "status": "finalized",
                                 "result": "no", "close_time": "T"}], "now")
    row = conn.execute("SELECT * FROM settlements WHERE ticker='A'").fetchone()
    assert (row["status"], row["result"]) == ("finalized", "no")


def test_store_writes_a_null_row_for_a_ticker_the_exchange_omitted(conn):
    settle._store(conn, ["A", "B"], [{"ticker": "A", "status": "active"}], "n")
    row = conn.execute("SELECT * FROM settlements WHERE ticker='B'").fetchone()
    assert row["status"] is None


def test_resume_point_advances_even_when_a_ticker_was_omitted(conn):
    settle._store(conn, ["A", "B"], [{"ticker": "A", "status": "active"}], "n")
    assert settle._resume_point(conn) == "B"


def test_run_walks_every_ticker_once(conn, monkeypatch):
    seen = []

    def fake(tickers):
        seen.extend(tickers)
        return [{"ticker": t, "status": "finalized", "result": "yes"}
                for t in tickers]

    monkeypatch.setattr(settle.kalshi, "markets_by_tickers", fake)
    monkeypatch.setattr(settle, "BATCH", 2)
    counts = settle.run(conn)
    assert seen == ["A", "B", "C"]
    assert counts["requested"] == 3


def test_run_resumes_without_refetching_finished_work(conn, monkeypatch):
    settle._store(conn, ["A", "B"], [], "n")
    seen = []
    monkeypatch.setattr(settle.kalshi, "markets_by_tickers",
                        lambda t: seen.extend(t) or [])
    settle.run(conn)
    assert seen == ["C"]


def test_run_stops_cleanly_when_rate_limited(conn, monkeypatch):
    def boom(tickers):
        raise settle.kalshi.RateLimited("/markets")

    monkeypatch.setattr(settle.kalshi, "markets_by_tickers", boom)
    assert settle.run(conn)["requested"] == 0


def test_markets_by_tickers_refuses_an_oversized_batch():
    with pytest.raises(ValueError):
        settle.kalshi.markets_by_tickers(["T"] * 101)
