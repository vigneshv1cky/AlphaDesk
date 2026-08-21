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
    monkeypatch.setattr(prices, "intraday_bars", lambda sym, start, interval="1m": [
        {"ts": FakeBar(i, 100.0).timestamp, "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.5, "volume": 1234.0 + i} for i in range(60)])
    prices._chart_cache.clear()


class TestChartVolume:
    def test_bars_carry_volume(self, stub_bars):
        series = prices.get_chart_series("TEST", days=2, range_key="1D")
        assert series is not None
        assert set(series["bars"][0]) == {"t", "o", "h", "l", "c", "v"}
        assert series["bars"][0]["v"] == 1234.0

    def test_a_feed_without_volume_reports_zero_not_a_missing_key(self, monkeypatch):
        """The histogram reads `v` on every bar; absent is worse than zero."""
        monkeypatch.setattr(prices, "intraday_bars", lambda sym, start, interval="1m": [
            {"ts": FakeBar(i, 100.0).timestamp, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.5} for i in range(60)])
        prices._chart_cache.clear()
        series = prices.get_chart_series("TEST", days=2, range_key="1D")
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


class TestIntervalResolution:
    """A too-fine interval for the span is DOWNGRADED, not refused — and the
    response says what was actually served, so nobody reads an hourly chart
    believing it is minute data."""

    def test_a_short_range_keeps_the_fine_interval(self):
        assert prices.resolve_interval("1D", "1m") == "1m"

    def test_minute_bars_over_a_year_fall_back(self):
        got = prices.resolve_interval("1Y", "1m")
        assert got == "1h", "the finest interval that actually covers a year"

    def test_a_five_year_range_falls_all_the_way_to_daily(self):
        assert prices.resolve_interval("5Y", "15m") == "1d"

    def test_no_preference_picks_by_span(self):
        assert prices.resolve_interval("1D", None) == "1m"
        assert prices.resolve_interval("1Y", None) == "1d"

    def test_an_unknown_interval_is_treated_as_no_preference(self):
        assert prices.resolve_interval("1D", "banana") == "1m"

    def test_the_series_reports_what_it_served(self, monkeypatch):
        monkeypatch.setattr(prices, "intraday_bars", lambda sym, start, interval="1m": [
            {"ts": FakeBar(i, 100.0).timestamp, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.5, "volume": 1.0} for i in range(60)])
        prices._chart_cache.clear()
        series = prices.get_chart_series("TEST", range_key="1D", interval="15m")
        assert series["interval"] == "15m"
        assert series["interval_label"] == "15 mins"
        assert series["interval_requested"] == "15m"


class TestOfferableIntervals:
    """Which intervals a range is allowed to OFFER.

    Distinct from resolve_interval, which answers what a request gets served.
    This decides what may be asked for at all, and it exists because the
    answers differ wildly in cost: measured warm, 3M of hourly returns in 0.7s
    while 1Y of hourly takes 9-14s and draws 2,031 points into a 449px tile.
    """

    def test_every_range_offers_its_own_default(self):
        # The toolbar shows the served interval as the current value, so a
        # default missing from its own menu would render a selection that
        # cannot be re-selected.
        for rng in ("1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"):
            served = prices.resolve_interval(rng, None)
            assert served in prices.available_intervals(rng), f"{rng} omits {served}"

    def test_nothing_offered_would_be_downgraded(self):
        # The whole point: an offered interval must come back as itself, so the
        # "showing X instead" notice becomes unreachable rather than routine.
        for rng in ("1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"):
            for iv in prices.available_intervals(rng):
                assert prices.resolve_interval(rng, iv) == iv, f"{rng}/{iv} downgrades"

    def test_intraday_stops_after_three_months(self):
        # The expensive band. 6M/YTD/1Y hourly cost seconds and are unreadable
        # at tile width, so they are not offered at all.
        for rng in ("6M", "YTD", "1Y", "5Y", "MAX"):
            for iv in prices.available_intervals(rng):
                assert prices._interval_minutes(iv) is None, f"{rng} still offers {iv}"

    def test_three_months_keeps_hourly(self):
        # Deliberately retained: 0.7s, 1.25 bars per pixel, and the only route
        # to intraday structure in an older period is zooming a longer series.
        assert "1h" in prices.available_intervals("3M")

    def test_no_range_offers_a_handful_of_bars(self):
        for rng in ("1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"):
            span = prices.RANGE_DAYS[rng]
            for iv in prices.available_intervals(rng):
                assert prices._estimated_bars(span, iv) >= prices._MIN_OFFERABLE_BARS

    def test_every_range_offers_something(self):
        for rng in ("1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"):
            assert prices.available_intervals(rng), f"{rng} offers nothing"

    def test_unknown_range_is_treated_as_a_day(self):
        assert prices.available_intervals("nonsense") == prices.available_intervals("1D")

    def test_estimate_tracks_the_window_actually_fetched(self):
        # Intraday is fetched over `span + 3` CALENDAR days, not over the
        # range's nominal length. Estimating from the nominal length read 1D as
        # a single session and made 30-minute bars look too sparse to offer
        # when they are ~50. Measured hourly counts, which the estimate must
        # stay close to:
        for rng, served in (("1M", 197), ("3M", 560), ("6M", 1094), ("1Y", 2031)):
            est = prices._estimated_bars(prices.RANGE_DAYS[rng], "1h")
            assert 0.75 * served <= est <= 1.25 * served, f"{rng}: est {est} vs {served}"

    def test_one_day_still_offers_its_finer_intervals(self):
        # The regression the estimator fix exists to prevent.
        offered = prices.available_intervals("1D")
        for iv in ("15m", "30m"):
            assert iv in offered, f"1D dropped {iv}"

    def test_monthly_bars_need_more_than_a_year(self):
        # Twelve points is not a chart. Five years of them is.
        for rng in ("YTD", "1Y"):
            assert "1mo" not in prices.available_intervals(rng)
        for rng in ("5Y", "MAX"):
            assert "1mo" in prices.available_intervals(rng)

    def test_four_hour_bars_survive_a_month(self):
        # ~59 bars: roughly two a session. Thin-looking but perfectly readable,
        # and the floor must not be raised so far that it takes this with it.
        assert "4h" in prices.available_intervals("1M")
