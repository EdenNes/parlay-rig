import logging
import time
from typing import Any, Dict, List, Optional

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
TIMEOUT = 10
THROTTLE_SECONDS = 0.08
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
                # Token bucket refills continuously (docs.kalshi.com rate
                # limits page): wait for tokens, then retry. Raise only if
                # the bucket stays dry across all attempts.
                if attempt == 2:
                    raise RateLimited(path)
                time.sleep(2.0 * (attempt + 1))
                continue
            resp.raise_for_status()
            time.sleep(THROTTLE_SECONDS)
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


# 100 tickers per request is the empirical ceiling: 200 returns HTTP 414
# (URI too long) from the edge proxy before the API ever sees it.
MAX_TICKERS_PER_CALL = 100


def markets_by_tickers(tickers: List[str]) -> List[Dict[str, Any]]:
    if len(tickers) > MAX_TICKERS_PER_CALL:
        raise ValueError("at most %d tickers per call" % MAX_TICKERS_PER_CALL)
    page = _get("/markets", {"tickers": ",".join(tickers), "limit": 1000})
    return page.get("markets", [])


def candlesticks(series: str, ticker: str, start_ts: int,
                 end_ts: int, period_interval: int = 1) -> Dict[str, Any]:
    return _get("/series/%s/markets/%s/candlesticks" % (series, ticker),
                {"start_ts": start_ts, "end_ts": end_ts,
                 "period_interval": period_interval})
