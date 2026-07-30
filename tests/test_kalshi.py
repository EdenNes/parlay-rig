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


def test_get_raises_ratelimited_after_persistent_429(monkeypatch):
    monkeypatch.setattr(
        kalshi._session, "get",
        lambda url, params=None, timeout=None: FakeResp(429, {}))
    monkeypatch.setattr(kalshi.time, "sleep", lambda s: None)
    with pytest.raises(kalshi.RateLimited):
        kalshi._get("/x")


def test_get_recovers_from_transient_429(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            return FakeResp(429, {})
        return FakeResp(200, {"ok": True})

    monkeypatch.setattr(kalshi._session, "get", fake_get)
    monkeypatch.setattr(kalshi.time, "sleep", lambda s: None)
    assert kalshi._get("/x") == {"ok": True}


@pytest.mark.slow
def test_live_trades_page_has_trades_key():
    page = kalshi.trades_page(min_ts=int(time.time()) - 600)
    assert "trades" in page
