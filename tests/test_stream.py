"""The live-trade stream's bookkeeping.

Everything here runs without a socket. What is worth testing is not that
Alpaca can push a trade — it can — but that the process holds exactly one
upstream subscription per symbol somebody is watching, and none at all for
symbols nobody is. The free tier allows a single concurrent connection, so a
leaked reference is not a tidiness problem: it is a subscription that outlives
its reader and a connection slot that cannot be reclaimed.
"""

import time

import pytest

from alphadesk.ingest import stream as stream_mod


class FakeStream:
    """Stands in for alpaca-py's StockDataStream, recording what it was told."""

    def __init__(self):
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    def subscribe_trades(self, handler, *symbols):
        self.subscribed.extend(symbols)

    def unsubscribe_trades(self, *symbols):
        self.unsubscribed.extend(symbols)


@pytest.fixture
def market(monkeypatch):
    """A stream whose upstream is a fake and which never starts a thread."""
    m = stream_mod._MarketStream()
    fake = FakeStream()
    monkeypatch.setattr(m, "_ensure_running", lambda: (setattr(m, "_stream", fake) or True)
                        if m._stream is None else True)
    m._fake = fake                     # type: ignore[attr-defined]
    return m


class TestSubscriptionRefcount:
    def test_first_reader_subscribes_upstream(self, market):
        assert market.acquire("nvda") is True
        assert market._fake.subscribed == ["NVDA"]
        assert market.status()["symbols"] == {"NVDA": 1}

    def test_second_reader_does_not_resubscribe(self, market):
        # Two panels on one board share the upstream subscription; asking
        # twice must not send a second subscribe.
        market.acquire("NVDA")
        market.acquire("NVDA")
        assert market._fake.subscribed == ["NVDA"]
        assert market.status()["symbols"] == {"NVDA": 2}

    def test_upstream_survives_until_the_last_reader_goes(self, market):
        market.acquire("NVDA")
        market.acquire("NVDA")
        market.release("NVDA")
        assert market._fake.unsubscribed == []      # one reader is still here
        assert market.status()["symbols"] == {"NVDA": 1}
        market.release("NVDA")
        assert market._fake.unsubscribed == ["NVDA"]
        assert market.status()["symbols"] == {}

    def test_releasing_something_never_acquired_is_harmless(self, market):
        market.release("NVDA")                      # e.g. a disconnect after a failed acquire
        assert market.status()["symbols"] == {}

    def test_symbols_are_normalised(self, market):
        market.acquire("nvda")
        market.release("NvDa")
        assert market.status()["symbols"] == {}

    def test_releasing_drops_the_cached_tick(self, market):
        market.acquire("NVDA")
        market._last["NVDA"] = {"symbol": "NVDA", "price": 1.0, "received": time.time()}
        market.release("NVDA")
        # Otherwise the next reader of that symbol is handed a price from
        # whenever the previous one was last looking.
        assert market.latest("NVDA") is None


class TestTickFreshness:
    def test_unseen_symbol_reports_nothing(self, market):
        # None is the honest answer on a feed that prints a few percent of
        # volume — not an error, and not a reason to show a stale number.
        assert market.latest("NVDA") is None

    def test_a_fresh_tick_is_not_stale(self, market):
        market._last["NVDA"] = {"symbol": "NVDA", "price": 1.0, "received": time.time()}
        tick = market.latest("NVDA")
        assert tick and tick["stale"] is False and tick["age_s"] < 1

    def test_an_old_tick_is_marked_stale_rather_than_hidden(self, market):
        old = time.time() - stream_mod.TICK_STALE_AFTER_S - 1
        market._last["NVDA"] = {"symbol": "NVDA", "price": 1.0, "received": old}
        tick = market.latest("NVDA")
        # Still returned: the caller decides whether to show it greyed or drop
        # it, and "the last print was two minutes ago" is itself information.
        assert tick and tick["stale"] is True


class TestUnavailableUpstream:
    def test_no_credentials_means_no_live_data_not_an_error(self, monkeypatch):
        m = stream_mod._MarketStream()
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        assert m.acquire("NVDA") is False
        assert m.status() == {"connected": False, "available": False, "symbols": {}}

    def test_a_failed_start_is_not_retried_on_every_request(self, monkeypatch):
        m = stream_mod._MarketStream()
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        m.acquire("NVDA")
        calls = []
        monkeypatch.setattr(stream_mod.log, "info", lambda *a, **k: calls.append(a))
        for _ in range(5):
            assert m.acquire("NVDA") is False
        # Already known to be unavailable; it must not re-probe (and re-log)
        # once per reader per reconnect.
        assert calls == []
