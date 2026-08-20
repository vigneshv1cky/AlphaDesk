"""The shapes the markets board renders from.

Both additions here are things the UI draws per row or per bar, dozens of
times, so a silently-missing field is a page full of holes rather than one
visible error. These pin the contract without touching the network.
"""

from datetime import datetime, timedelta

import pytest

from alphadesk.config import ET
from alphadesk.ingest import prices


class FakeBar:
    def __init__(self, i: int, px: float):
        self.timestamp = datetime(2026, 8, 19, 10, 0, tzinfo=ET) + timedelta(minutes=i)
        self.open = px
        self.high = px + 0.5
        self.low = px - 0.5
        self.close = px + 0.2
        self.volume = 1000 + i


class FakeResp:
    def __init__(self, data):
        self.data = data


@pytest.fixture()
def stub_bars(monkeypatch):
    """Every intraday bar carries a volume, as the real feed does."""
    monkeypatch.setattr(prices, "intraday_bars", lambda sym, start: [
        {"ts": FakeBar(i, 100.0).timestamp, "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.5, "volume": 1234.0 + i} for i in range(60)])
    prices._chart_cache.clear()


class TestChartVolume:
    def test_bars_carry_volume(self, stub_bars):
        series = prices.get_chart_series("TEST", days=2)
        assert series is not None
        assert set(series["bars"][0]) == {"t", "o", "h", "l", "c", "v"}
        assert series["bars"][0]["v"] == 1234.0

    def test_a_feed_without_volume_reports_zero_not_a_missing_key(self, monkeypatch):
        """The histogram reads `v` on every bar; absent is worse than zero."""
        monkeypatch.setattr(prices, "intraday_bars", lambda sym, start: [
            {"ts": FakeBar(i, 100.0).timestamp, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.5} for i in range(60)])
        prices._chart_cache.clear()
        series = prices.get_chart_series("TEST", days=2)
        assert all("v" in b for b in series["bars"])
        assert series["bars"][0]["v"] == 0.0


class TestSparkSeries:
    def _client(self, monkeypatch, bars_per_symbol: int):
        class FakeClient:
            def get_stock_bars(self, req):
                return FakeResp({s: [FakeBar(i, 50.0 + i) for i in range(bars_per_symbol)]
                                 for s in req.symbol_or_symbols})
        monkeypatch.setattr(prices, "_alpaca_data_client", lambda: FakeClient())

    def test_returns_a_close_series_per_symbol(self, monkeypatch):
        self._client(monkeypatch, 60)
        out = prices._spark_series(["AAA", "BBB"])
        assert sorted(out) == ["AAA", "BBB"]
        assert out["AAA"] == sorted(out["AAA"]), "closes should keep feed order"

    def test_series_is_capped(self, monkeypatch):
        self._client(monkeypatch, 400)
        out = prices._spark_series(["AAA"])
        assert len(out["AAA"]) == prices._SPARK_POINTS, "a 64px line needs no more"

    def test_a_single_point_is_dropped(self, monkeypatch):
        """One point draws a flat line, which reads as 'did not move' rather
        than the truth, 'not enough data' — the row should show nothing."""
        self._client(monkeypatch, 1)
        assert prices._spark_series(["AAA"]) == {}

    def test_no_client_degrades_to_empty(self, monkeypatch):
        monkeypatch.setattr(prices, "_alpaca_data_client", lambda: None)
        assert prices._spark_series(["AAA"]) == {}

    def test_a_raising_feed_degrades_to_empty(self, monkeypatch):
        class Boom:
            def get_stock_bars(self, req):
                raise RuntimeError("upstream down")
        monkeypatch.setattr(prices, "_alpaca_data_client", lambda: Boom())
        assert prices._spark_series(["AAA"]) == {}, "a dead spark feed must not fail the board"


class TestMoversCarrySparks:
    def test_every_rendered_row_has_a_spark_key(self, monkeypatch):
        """Present on every row even when the fetch found nothing — the UI
        reads `spark` unconditionally."""
        monkeypatch.setattr(prices, "_spark_series", lambda syms: {"AAA": [1.0, 2.0]})
        monkeypatch.setattr(prices, "_snapshot_prices", lambda syms: {
            s: {"price": 50.0, "change_pct": 1.0, "volume": 10_000_000,
                "dollar_volume": 500_000_000} for s in syms})

        class Row:
            def __init__(self, sym):
                self.symbol = sym
                self.percent_change = 1.0
                self.volume = 10_000_000

        class FakeScreener:
            def get_most_actives(self, req):
                return type("R", (), {"most_actives": [Row("AAA"), Row("BBB")]})()

            def get_market_movers(self, req):
                return type("R", (), {"gainers": [Row("AAA")], "losers": []})()

        import alphadesk.ingest.prices as mod
        monkeypatch.setattr(mod, "_movers_cache", (0.0, None), raising=False)
        monkeypatch.setitem(__import__("os").environ, "ALPACA_API_KEY", "k")
        monkeypatch.setitem(__import__("os").environ, "ALPACA_SECRET_KEY", "s")
        monkeypatch.setattr(
            "alpaca.data.historical.screener.ScreenerClient",
            lambda *a, **k: FakeScreener(),
        )

        out = prices.movers(top=10)
        rows = out["most_active"]
        assert rows, "the stubbed screener should produce rows"
        assert all("spark" in r for r in rows)
        assert rows[0]["spark"] == [1.0, 2.0]
        assert next(r for r in rows if r["symbol"] == "BBB")["spark"] == []
