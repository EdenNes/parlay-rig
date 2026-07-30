import db


def test_connect_creates_tables(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"markets", "trades", "leg_quotes", "census"} <= names


def test_connect_enables_wal(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_connect_is_idempotent(tmp_path):
    db.connect(str(tmp_path / "t.db"))
    conn = db.connect(str(tmp_path / "t.db"))
    assert conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0] == 0
