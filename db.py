import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
  ticker TEXT PRIMARY KEY,
  series TEXT,
  event_ticker TEXT,
  collection TEXT,
  legs_json TEXT,
  created_ts TEXT,
  close_ts TEXT,
  volume_fp REAL,
  oi_fp REAL,
  status TEXT,
  result TEXT,
  last_seen_ts TEXT
);
CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY,
  ticker TEXT,
  ts TEXT,
  yes_price REAL,
  count_fp REAL,
  taker_side TEXT
);
CREATE TABLE IF NOT EXISTS leg_quotes (
  leg_ticker TEXT,
  ts TEXT,
  yes_bid REAL,
  yes_ask REAL
);
CREATE INDEX IF NOT EXISTS idx_leg_quotes ON leg_quotes (leg_ticker, ts);
CREATE TABLE IF NOT EXISTS census (
  ts TEXT,
  series TEXT,
  n_markets INTEGER,
  total_volume_fp REAL
);
"""


def connect(path: str = "data/rig.db") -> sqlite3.Connection:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
