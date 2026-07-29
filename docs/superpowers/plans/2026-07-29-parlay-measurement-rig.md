# Parlay measurement rig implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A passive collector on the GCP VM that records every Kalshi parlay fill with leg prices, plus a scoring module (written by Eden) and a nightly report that measures how far real fills sit from coherent prices.

**Architecture:** Three small programs around one SQLite file. A cron-fired collector polls Kalshi's exchange-wide trade tape (verified live 2026-07-29: `GET /markets/trades` with `min_ts` and no ticker filter returns the global stream with a cursor), keeps KXMVE trades, and stores raw facts only. Scoring is pure math applied at report time. Phase 2 (candlestick backfill) is out of this plan and gets its own plan later.

**Tech Stack:** Python 3.9, requests (only dependency), sqlite3 stdlib, pytest.

## Global constraints

- Python 3.9 typing only: `Optional[X]`, `List[X]`, `Dict[K,V]`. Never `X | None`.
- requests is the only third-party runtime dependency. pytest for tests. No ORM, no framework.
- Every HTTP call: 10 s timeout, 3 attempts, exponential backoff (1 s, 2 s, 4 s).
- Logging: one line per collector cycle (start implied by timestamp, counts, duration).
- Tests: behavior over implementation, one assertion per test, no `if` or loops in tests.
- Git: feature branches + squash-merge. Never commit directly to main. No Co-Authored-By trailers.
- Authorship: Tasks 1-4, 6, 7 built by Claude with Eden following (learning mode on). Task 5 (`scoring.py`) is written by Eden personally; Claude tutors but does not write the implementation.
- README and all docs: no AI-writing tells (no em dashes, plain sentence-case headings).
- Deploy target: GCP VM `instance-20260505-163417`, zone `us-central1-f` (the weather-kalshi box). All VM paths under `~/parlay-rig/`.
- Working directory for all commands: `/Users/eden/Documents/code/parlay-rig-2026-07-29`.

---

### Task 1: db.py (schema + connection)

**Files:**
- Create: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `db.connect(path: str = "data/rig.db") -> sqlite3.Connection` with `row_factory = sqlite3.Row`, WAL mode on, all four tables created idempotently.

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feature/collector
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_db.py
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 4: Write db.py**

```python
# db.py
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_db.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: sqlite schema and connection helper"
```

---

### Task 2: kalshi.py (API client)

**Files:**
- Create: `kalshi.py`
- Test: `tests/test_kalshi.py`

**Interfaces:**
- Produces:
  - `kalshi.trades_page(min_ts: int, cursor: Optional[str] = None) -> Dict[str, Any]` returning the raw JSON page (`{"trades": [...], "cursor": "..."}`).
  - `kalshi.market(ticker: str) -> Dict[str, Any]` returning raw JSON (`{"market": {...}}`).
  - `kalshi.RateLimited` exception raised on HTTP 429.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kalshi.py
import time

import pytest
import requests

import kalshi


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


def test_get_retries_then_succeeds(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) < 3:
            raise requests.ConnectionError("boom")
        return FakeResp(200, {"ok": True})

    monkeypatch.setattr(kalshi._session, "get", fake_get)
    monkeypatch.setattr(kalshi.time, "sleep", lambda s: None)
    assert kalshi._get("/x") == {"ok": True}


def test_get_raises_ratelimited_on_429(monkeypatch):
    monkeypatch.setattr(
        kalshi._session, "get",
        lambda url, params=None, timeout=None: FakeResp(429, {}))
    with pytest.raises(kalshi.RateLimited):
        kalshi._get("/x")


@pytest.mark.slow
def test_live_trades_page_has_trades_key():
    page = kalshi.trades_page(min_ts=int(time.time()) - 600)
    assert "trades" in page
```

- [ ] **Step 2: Add pytest config so `slow` is a known marker and skipped by default**

```ini
# pytest.ini
[pytest]
markers =
    slow: hits the live Kalshi API
addopts = -m "not slow"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_kalshi.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kalshi'` (slow test deselected)

- [ ] **Step 4: Write kalshi.py**

```python
# kalshi.py
import logging
import time
from typing import Any, Dict, Optional

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
TIMEOUT = 10
log = logging.getLogger("rig")

_session = requests.Session()


class RateLimited(Exception):
    """HTTP 429: caller should end its cycle; the next cron tick retries."""


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    delay = 1.0
    for attempt in range(3):
        try:
            resp = _session.get(BASE + path, params=params, timeout=TIMEOUT)
            if resp.status_code == 429:
                raise RateLimited(path)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
            if attempt == 2:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def trades_page(min_ts: int, cursor: Optional[str] = None) -> Dict[str, Any]:
    params = {"limit": 1000, "min_ts": min_ts}  # type: Dict[str, Any]
    if cursor:
        params["cursor"] = cursor
    return _get("/markets/trades", params)


def market(ticker: str) -> Dict[str, Any]:
    return _get("/markets/" + ticker)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_kalshi.py -v`
Expected: 2 passed, 1 deselected

- [ ] **Step 6: Run the live smoke test once**

Run: `python3 -m pytest tests/test_kalshi.py -m slow -v`
Expected: 1 passed (requires network; if the laptop network blocks it, run after deploy on the VM instead)

- [ ] **Step 7: Commit**

```bash
git add kalshi.py tests/test_kalshi.py pytest.ini
git commit -m "feat: kalshi client with retry, backoff, 429 signal"
```

---

### Task 3: collector.py (one polling cycle)

**Files:**
- Create: `collector.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: `db.connect`, `kalshi.trades_page`, `kalshi.market`, `kalshi.RateLimited`.
- Produces: `collector.run_cycle(conn) -> Dict[str, int]` (counts: trades, markets, legs). `python3 collector.py` runs one cycle against `data/rig.db` and logs to `logs/collector.log`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_collector.py
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


def test_market_row_stores_legs_json(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    _fake_kalshi(monkeypatch, [TRADE])
    collector.run_cycle(conn)
    row = conn.execute("SELECT legs_json FROM markets").fetchone()
    assert len(json.loads(row["legs_json"])) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_collector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'collector'`

- [ ] **Step 3: Write collector.py**

```python
# collector.py
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
BOOTSTRAP_SECONDS = 3600
OVERLAP_SECONDS = 60


def _epoch(rfc3339: str) -> int:
    return int(datetime.fromisoformat(
        rfc3339.replace("Z", "+00:00")).timestamp())


def _min_ts(conn) -> int:
    row = conn.execute("SELECT MAX(ts) AS m FROM trades").fetchone()
    if row["m"] is None:
        return int(time.time()) - BOOTSTRAP_SECONDS
    return _epoch(row["m"]) - OVERLAP_SECONDS


def _new_mve_trades(min_ts: int) -> List[Dict[str, Any]]:
    out = []  # type: List[Dict[str, Any]]
    cursor = None
    while True:
        page = kalshi.trades_page(min_ts, cursor)
        batch = page.get("trades", [])
        out.extend(t for t in batch if t["ticker"].startswith(MVE_PREFIX))
        cursor = page.get("cursor")
        if not cursor or not batch:
            return out


def _ensure_market(conn, ticker: str, now_iso: str) -> Tuple[List[str], bool]:
    """Return (leg market tickers, was_newly_inserted)."""
    row = conn.execute(
        "SELECT legs_json FROM markets WHERE ticker = ?", (ticker,)).fetchone()
    if row is not None:
        legs = json.loads(row["legs_json"] or "[]")
        return [l["market_ticker"] for l in legs], False
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
    return [l["market_ticker"] for l in legs], True


def _num(value: Any) -> float:
    return float(value or 0)


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
    try:
        fresh = _new_mve_trades(_min_ts(conn))
    except kalshi.RateLimited:
        log.warning("rate limited, ending cycle early")
        return counts
    legs_to_quote = set()  # type: Set[str]
    for t in fresh:
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
    counts["legs"] = _snapshot_legs(conn, legs_to_quote, now_iso)
    _census(conn, now_iso)
    conn.commit()
    log.info("cycle done in %.1fs: %s", time.time() - started, counts)
    return counts


def main() -> int:
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(filename="logs/collector.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    conn = db.connect()
    run_cycle(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_collector.py -v`
Expected: 5 passed

- [ ] **Step 5: Run one real cycle locally and inspect**

Run: `python3 collector.py && python3 -c "import db; c = db.connect(); print(c.execute('SELECT COUNT(*) FROM trades').fetchone()[0], 'trades'); print(c.execute('SELECT COUNT(*) FROM leg_quotes').fetchone()[0], 'leg quotes')"`
Expected: nonzero trades within an hour of a live sports window; zero is acceptable at dead hours, in which case check `logs/collector.log` shows a completed cycle.

- [ ] **Step 6: Commit**

```bash
git add collector.py tests/test_collector.py
git commit -m "feat: collector cycle over the global trade tape"
```

---

### Task 4: merge, private repo, deploy to the VM

**Files:**
- Modify: none (git and VM operations)

**Interfaces:**
- Consumes: working collector from Tasks 1-3.
- Produces: collector running every 2 minutes on the VM, data accumulating in `~/parlay-rig/data/rig.db`.

- [ ] **Step 1: Squash-merge the collector branch**

```bash
git checkout main
git merge --squash feature/collector
git commit -m "feat: parlay fill collector (db, kalshi client, cycle)"
git branch -D feature/collector
```

- [ ] **Step 2: Create the private GitHub repo and push**

```bash
gh repo create parlay-rig --private --source . --push
```

Expected: repo `EdenNes/parlay-rig` created, main pushed.

- [ ] **Step 3: Copy the project to the VM**

```bash
gcloud compute scp --zone us-central1-f --recurse \
  db.py kalshi.py collector.py \
  instance-20260505-163417:~/parlay-rig/
```

If `~/parlay-rig` does not exist, first run:
`gcloud compute ssh instance-20260505-163417 --zone us-central1-f --command "mkdir -p ~/parlay-rig/logs"`

- [ ] **Step 4: Verify python3 and requests on the VM**

```bash
gcloud compute ssh instance-20260505-163417 --zone us-central1-f \
  --command "python3 --version && python3 -c 'import requests; print(requests.__version__)'"
```

If requests is missing: `pip3 install --user requests`.

- [ ] **Step 5: Run one cycle by hand on the VM**

```bash
gcloud compute ssh instance-20260505-163417 --zone us-central1-f \
  --command "cd ~/parlay-rig && python3 collector.py && tail -1 logs/collector.log"
```

Expected: a `cycle done` log line with counts.

- [ ] **Step 6: Install the cron line**

```bash
gcloud compute ssh instance-20260505-163417 --zone us-central1-f \
  --command "(crontab -l 2>/dev/null | grep -v parlay-rig; echo '*/2 * * * * cd ~/parlay-rig && /usr/bin/python3 collector.py >> logs/cron.log 2>&1') | crontab - && crontab -l"
```

Expected: crontab listing shows the parlay-rig line once.

- [ ] **Step 7: Wait 6+ minutes, confirm growth**

```bash
gcloud compute ssh instance-20260505-163417 --zone us-central1-f \
  --command "cd ~/parlay-rig && python3 -c \"import db; c = db.connect(); print('trades', c.execute('SELECT COUNT(*) FROM trades').fetchone()[0]); print('cycles', c.execute('SELECT COUNT(DISTINCT ts) FROM census').fetchone()[0])\""
```

Expected: cycles count of 3 or more and a growing trade count. Data is now accumulating; everything after this task can land at any pace.

---

### Task 5: scoring.py (EDEN WRITES THIS)

**Files:**
- Create: `scoring.py` (Eden)
- Test: `tests/test_scoring.py` (provided below, committed first as the contract)

**Interfaces:**
- Produces (exact signatures the report relies on):
  - `leg_prob(side: str, yes_price: float) -> float`
  - `frechet_ceiling(leg_probs: List[float]) -> float`
  - `frechet_floor(leg_probs: List[float]) -> float`
  - `independence_price(leg_probs: List[float]) -> float`
  - `score_fill(fill_price: float, leg_probs: List[float]) -> Dict[str, Any]` with keys `ceiling`, `floor`, `independence`, `gap_to_ceiling`, `gap_to_independence` (floats) and `coherent` (bool).

**Math contract (authoritative):** a leg taken on the yes side has probability `yes_price`; on the no side, `1 - yes_price`. Ceiling is the minimum leg probability. Floor is `max(0, sum(leg_probs) - (n - 1))`. Independence price is the product. Gaps are `fill_price` minus ceiling and minus independence (positive gap = fill above the reference). Coherent means `fill_price <= ceiling + 1e-9`.

**Authorship rule:** Claude commits the tests, explains any concept on request, reviews Eden's diffs, and never writes scoring.py's body. This is the module Eden defends on the call.

- [ ] **Step 1: Create the branch and commit the test contract**

```bash
git checkout -b feature/scoring
```

```python
# tests/test_scoring.py
from typing import List

import pytest

import scoring


def test_leg_prob_yes_side():
    assert scoring.leg_prob("yes", 0.82) == pytest.approx(0.82)


def test_leg_prob_no_side():
    assert scoring.leg_prob("no", 0.82) == pytest.approx(0.18)


def test_ceiling_is_min_leg():
    assert scoring.frechet_ceiling([0.82, 0.14]) == pytest.approx(0.14)


def test_floor_two_likely_legs():
    assert scoring.frechet_floor([0.8, 0.7]) == pytest.approx(0.5)


def test_floor_clamps_at_zero():
    assert scoring.frechet_floor([0.5, 0.4, 0.3]) == pytest.approx(0.0)


def test_independence_multiplies():
    assert scoring.independence_price([0.5, 0.4, 0.3]) == pytest.approx(0.06)


def test_study_fill_flagged_incoherent():
    assert scoring.score_fill(0.80, [0.82, 0.14])["coherent"] is False


def test_coherent_fill_flagged_true():
    assert scoring.score_fill(0.10, [0.82, 0.14])["coherent"] is True


def test_gap_to_independence_signed():
    assert scoring.score_fill(0.10, [0.5, 0.4])["gap_to_independence"] == pytest.approx(-0.10)
```

```bash
git add tests/test_scoring.py
git commit -m "test: scoring contract (Frechet bounds, independence, score_fill)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scoring.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scoring'`

- [ ] **Step 3: Eden writes scoring.py until all 9 tests pass**

Run after each edit: `python3 -m pytest tests/test_scoring.py -v`
Expected end state: 9 passed. Claude tutors on request only.

- [ ] **Step 4: Eden commits his module**

```bash
git add scoring.py
git commit -m "feat: scoring module (written by Eden)"
```

- [ ] **Step 5: Squash-merge**

```bash
git checkout main
git merge --squash feature/scoring
git commit -m "feat: parlay scoring module"
git branch -D feature/scoring
git push
```

---

### Task 6: report.py (join, score, markdown)

**Files:**
- Create: `report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `db.connect`, `scoring.leg_prob`, `scoring.score_fill` (exact signatures from Task 5).
- Produces: `report.build(conn) -> str` (the markdown text) and `python3 report.py` writing `reports/YYYY-MM-DD.md`.

- [ ] **Step 1: Create the branch and write the failing test**

```bash
git checkout -b feature/report
```

```python
# tests/test_report.py
import json

import db
import report


def _seed(conn):
    legs = [
        {"market_ticker": "LEG1", "side": "yes"},
        {"market_ticker": "LEG2", "side": "yes"},
    ]
    conn.execute(
        "INSERT INTO markets (ticker, series, legs_json) VALUES (?,?,?)",
        ("KXMVETEST-S1-ABC", "KXMVETEST", json.dumps(legs)))
    conn.execute(
        "INSERT INTO trades (trade_id, ticker, ts, yes_price, count_fp, "
        "taker_side) VALUES (?,?,?,?,?,?)",
        ("t-1", "KXMVETEST-S1-ABC", "2026-07-29T20:00:00+00:00", 0.80, 10, "yes"))
    conn.execute(
        "INSERT INTO leg_quotes (leg_ticker, ts, yes_bid, yes_ask) "
        "VALUES (?,?,?,?)", ("LEG1", "2026-07-29T20:00:30+00:00", 0.80, 0.84))
    conn.execute(
        "INSERT INTO leg_quotes (leg_ticker, ts, yes_bid, yes_ask) "
        "VALUES (?,?,?,?)", ("LEG2", "2026-07-29T20:00:30+00:00", 0.12, 0.16))
    conn.commit()


def test_incoherent_fill_is_counted(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    _seed(conn)
    text = report.build(conn)
    assert "1 incoherent of 1 scored" in text
```

The seeded fill reproduces the study case: fill at 0.80 with leg mids 0.82 and 0.14; ceiling 0.14, so incoherent.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'report'`

- [ ] **Step 3: Write report.py**

```python
# report.py
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import db
import scoring

QUOTE_WINDOW_SECONDS = 600


def _epoch(ts: str) -> int:
    return int(datetime.fromisoformat(
        ts.replace("Z", "+00:00")).timestamp())


def _nearest_mid(conn, leg_ticker: str, fill_ts: str) -> Optional[float]:
    fill_e = _epoch(fill_ts)
    best = None  # type: Optional[Dict[str, Any]]
    for row in conn.execute(
            "SELECT ts, yes_bid, yes_ask FROM leg_quotes WHERE leg_ticker = ?",
            (leg_ticker,)):
        dist = abs(_epoch(row["ts"]) - fill_e)
        if dist <= QUOTE_WINDOW_SECONDS and (best is None or dist < best["dist"]):
            best = {"dist": dist, "bid": row["yes_bid"], "ask": row["yes_ask"]}
    if best is None or (best["bid"] == 0 and best["ask"] == 0):
        return None
    return (best["bid"] + best["ask"]) / 2.0


def _scored_fills(conn) -> List[Dict[str, Any]]:
    out = []  # type: List[Dict[str, Any]]
    rows = conn.execute(
        "SELECT t.trade_id, t.ticker, t.ts, t.yes_price, t.count_fp, "
        "m.series, m.legs_json FROM trades t "
        "JOIN markets m ON m.ticker = t.ticker").fetchall()
    for r in rows:
        legs = json.loads(r["legs_json"] or "[]")
        probs = []  # type: List[float]
        for leg in legs:
            mid = _nearest_mid(conn, leg["market_ticker"], r["ts"])
            if mid is None:
                probs = []
                break
            probs.append(scoring.leg_prob(leg["side"], mid))
        if not probs:
            continue
        s = scoring.score_fill(r["yes_price"], probs)
        s.update({"series": r["series"], "ts": r["ts"],
                  "count_fp": r["count_fp"], "fill": r["yes_price"]})
        out.append(s)
    return out


def build(conn) -> str:
    scored = _scored_fills(conn)
    total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    incoherent = [s for s in scored if not s["coherent"]]
    lines = ["# Parlay pricing report", ""]
    lines.append("Generated %s" % datetime.now(timezone.utc).isoformat())
    lines.append("")
    lines.append("%d fills stored, %d scored (leg quote within %d s)."
                 % (total, len(scored), QUOTE_WINDOW_SECONDS))
    lines.append("%d incoherent of %d scored (fill above the Frechet ceiling)."
                 % (len(incoherent), len(scored)))
    if scored:
        avg_gap = sum(s["gap_to_independence"] for s in scored) / len(scored)
        lines.append("Average gap to independence price: %+.4f" % avg_gap)
    lines.append("")
    lines.append("## By series")
    lines.append("")
    by_series = {}  # type: Dict[str, List[Dict[str, Any]]]
    for s in scored:
        by_series.setdefault(s["series"], []).append(s)
    for series in sorted(by_series):
        group = by_series[series]
        bad = sum(1 for s in group if not s["coherent"])
        lines.append("- %s: %d scored, %d incoherent" % (series, len(group), bad))
    lines.append("")
    lines.append("## Volume census (latest cycle)")
    lines.append("")
    for row in conn.execute(
            "SELECT series, n_markets, total_volume_fp FROM census "
            "WHERE ts = (SELECT MAX(ts) FROM census) ORDER BY series"):
        lines.append("- %s: %d markets seen, %.0f contracts observed"
                     % (row["series"], row["n_markets"], row["total_volume_fp"]))
    return "\n".join(lines) + "\n"


def main() -> int:
    conn = db.connect()
    os.makedirs("reports", exist_ok=True)
    out = "reports/%s.md" % datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(out, "w") as f:
        f.write(build(conn))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_report.py -v`
Expected: 1 passed

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -v`
Expected: all tests pass, slow deselected

- [ ] **Step 6: Commit**

```bash
git add report.py tests/test_report.py
git commit -m "feat: daily pricing report (join, score, markdown)"
```

---

### Task 7: README, report cron, finish

**Files:**
- Create: `README.md`
- Modify: VM crontab

**Interfaces:**
- Consumes: everything prior.
- Produces: nightly report on the VM, documented repo, all branches merged.

- [ ] **Step 1: Write README.md**

```markdown
# parlay-rig

Passive measurement of parlay (combo) pricing on Kalshi, from the market
maker's side. Collects the public trade tape for KXMVE (multivariate event)
markets, snapshots each fill's leg prices, and scores every fill against the
Frechet ceiling and the independence price. Quotes nothing, trades nothing,
uses no credentials.

## Layout

- collector.py: one polling cycle over the exchange-wide trade tape
  (GET /markets/trades with min_ts), cron-fired every 2 minutes.
- scoring.py: pure math. Frechet bounds, independence price, per-fill gaps.
- report.py: nightly markdown report from the stored raw facts.
- db.py: SQLite schema (markets, trades, leg_quotes, census), WAL mode.

## Run

    python3 collector.py       # one cycle into data/rig.db
    python3 report.py          # writes reports/YYYY-MM-DD.md
    python3 -m pytest          # unit tests (live smoke: -m slow)

## Known limits

- Leg quotes lag fills by up to about 2 minutes (poll cadence).
- Scalar legs (outcomes multiplied) are excluded from scoring; binary only.
- Public tape only: RFQ quotes that never filled are invisible.

Design and plan: docs/superpowers/.
```

- [ ] **Step 2: Commit and squash-merge the report branch**

```bash
git add README.md
git commit -m "docs: readme"
git checkout main
git merge --squash feature/report
git commit -m "feat: pricing report and readme"
git branch -D feature/report
git push
```

- [ ] **Step 3: Ship report.py and scoring.py to the VM, add nightly cron**

```bash
gcloud compute scp --zone us-central1-f scoring.py report.py \
  instance-20260505-163417:~/parlay-rig/
gcloud compute ssh instance-20260505-163417 --zone us-central1-f \
  --command "(crontab -l 2>/dev/null | grep -v 'report.py'; echo '55 23 * * * cd ~/parlay-rig && /usr/bin/python3 report.py >> logs/cron.log 2>&1') | crontab - && crontab -l"
```

- [ ] **Step 4: Generate one report on demand from real collected data**

```bash
gcloud compute ssh instance-20260505-163417 --zone us-central1-f \
  --command "cd ~/parlay-rig && python3 report.py && cat reports/*.md | head -30"
```

Expected: a report with nonzero stored fills (scored count depends on quote coverage since deploy).

- [ ] **Step 5: Final check**

Run locally: `python3 -m pytest -v` (all pass), `git status` (clean), `git log --oneline` (bootstrap, collector, scoring, report commits on main).

---

## Self-review notes

- Spec coverage: collector (Tasks 1-4), scoring (Task 5), report (Task 6), deploy and cron (Tasks 4 and 7), census (inside collector), known limits in README (Task 7). Phase 2 backfill is explicitly out of scope per the spec and gets its own plan.
- The scoring implementation body is intentionally absent: the math contract and tests fully specify behavior, and authorship belongs to Eden by design.
- Signature consistency checked: `leg_prob`, `score_fill`, `trades_page`, `market`, `db.connect` match across Tasks 2, 3, 5, 6.
- Global-tape endpoint behavior (no ticker filter, min_ts, cursor) verified live on 2026-07-29 before this plan was written.
