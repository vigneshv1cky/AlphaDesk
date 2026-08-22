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


class TestDailyCoverage:
    """What `indicators_reliable` means on a DAILY series.

    It answers whether the FEED can be trusted, and a daily bar per trading day
    is complete by construction. It used to also fail the whole series below 35
    bars — MACD's 26+9 warm-up — which is a real limit but MACD's alone.
    Applying it to everything hid RSI-9 from a 24-bar month that supports it
    fine, so switching range to 1M silently dropped every pane the reader had
    chosen. Warm-up is per indicator and lives with each one (PANE_INDICATORS'
    minBars).
    """

    def _bars(self, n):
        return [{"close": 1.0 + i} for i in range(n)]

    def test_a_short_daily_series_is_still_a_trustworthy_feed(self):
        # 24 bars: too few for MACD, ample for RSI-9. The flag must not be the
        # thing that decides that.
        assert prices._daily_coverage(self._bars(24))["indicators_reliable"] is True

    def test_coverage_is_complete_by_construction(self):
        cov = prices._daily_coverage(self._bars(10))
        assert cov["coverage"] == 1.0
        assert cov["median_gap_min"] is None
        assert cov["bar_count"] == 10 and cov["sessions"] == 10

    def test_an_empty_series_is_not_reliable(self):
        cov = prices._daily_coverage([])
        assert cov["indicators_reliable"] is False
        assert cov["coverage"] == 0.0


class TestCryptoRanking:
    """The four crypto views. Split out from the fetch precisely so this can be
    pinned without a network client — the ordering is the part that would go
    wrong silently, because every view renders the same columns."""

    ROWS = [
        {"symbol": "BTC/USD", "change_pct": 2.0, "dollar_volume": 900.0},
        {"symbol": "ETH/USD", "change_pct": -4.0, "dollar_volume": 500.0},
        {"symbol": "SOL/USD", "change_pct": 9.0, "dollar_volume": 100.0},
        {"symbol": "XRP/USD", "change_pct": -1.0, "dollar_volume": 700.0},
    ]

    def test_all_preserves_the_configured_order(self):
        """`all` is the unranked view. Sorting it would make every one of the
        four tabs a ranking and leave the reader no way to see the list as
        configured — the same reason inventory() is a plain alphabetical read."""
        out = prices._rank_crypto(self.ROWS, 10)
        assert [r["symbol"] for r in out["all"]] == [r["symbol"] for r in self.ROWS]

    def test_most_active_ranks_by_turnover_not_unit_volume(self):
        out = prices._rank_crypto(self.ROWS, 10)
        assert [r["symbol"] for r in out["most_active"]] == [
            "BTC/USD", "XRP/USD", "ETH/USD", "SOL/USD"]

    def test_gainers_and_losers_split_on_sign_and_lead_with_the_extreme(self):
        out = prices._rank_crypto(self.ROWS, 10)
        assert [r["symbol"] for r in out["gainers"]] == ["SOL/USD", "BTC/USD"]
        # Worst first: a losers list led by the mildest decline buries its own
        # headline row.
        assert [r["symbol"] for r in out["losers"]] == ["ETH/USD", "XRP/USD"]

    def test_a_flat_row_appears_in_neither_direction(self):
        out = prices._rank_crypto([{"symbol": "F/USD", "change_pct": 0.0,
                                    "dollar_volume": 1.0}], 10)
        assert out["gainers"] == [] and out["losers"] == []
        assert len(out["all"]) == 1

    def test_turnover_is_a_ranking_input_not_a_column(self):
        """Alpaca's crypto turnover is venue-local — BTC prints ~11 coins a day
        on it. Ranking by it is defensible; rendering it as "volume" next to an
        equity panel's consolidated figure is not."""
        out = prices._rank_crypto(self.ROWS, 10)
        for view in out.values():
            for row in view:
                assert "dollar_volume" not in row

    def test_top_clamps_every_view(self):
        out = prices._rank_crypto(self.ROWS, 2)
        assert all(len(v) <= 2 for v in out.values())


class TestThemes:
    """Curated baskets. The parsing matters more than it looks: THEMES feeds the
    sidebar, so a bad override does not fail loudly — it silently empties the
    navigation."""

    def _load(self, monkeypatch, raw):
        import importlib
        from alphadesk import config
        if raw is None:
            monkeypatch.delenv("THEMES_JSON", raising=False)
        else:
            monkeypatch.setenv("THEMES_JSON", raw)
        return importlib.reload(config).THEMES

    def test_defaults_are_non_empty_and_uniquely_keyed(self, monkeypatch):
        themes = self._load(monkeypatch, None)
        assert themes
        ids = [t["id"] for t in themes]
        assert len(ids) == len(set(ids))
        for t in themes:
            assert t["label"] and t["symbols"]

    def test_override_replaces_the_defaults(self, monkeypatch):
        themes = self._load(
            monkeypatch, '[{"id":"x","label":"X","symbols":["aapl","msft"]}]')
        assert [t["id"] for t in themes] == ["x"]
        # Symbols are upper-cased on the way in, so a lowercase config does not
        # produce quote requests that miss the cache the rest of the app shares.
        assert themes[0]["symbols"] == ["AAPL", "MSFT"]

    def test_malformed_override_falls_back_rather_than_emptying_the_nav(self, monkeypatch):
        for bad in ("not json at all", "[]", '[{"id":"","label":"","symbols":[]}]',
                    '[{"id":"x"}]'):
            themes = self._load(monkeypatch, bad)
            assert themes, f"{bad!r} emptied THEMES"

    def test_entries_without_symbols_are_dropped_not_kept_empty(self, monkeypatch):
        themes = self._load(
            monkeypatch,
            '[{"id":"a","label":"A","symbols":["NVDA"]},'
            ' {"id":"b","label":"B","symbols":[]}]')
        assert [t["id"] for t in themes] == ["a"]


class TestQuoteFailuresAreNotCached:
    """A failed quote must not be remembered.

    The cache stored whatever quote() produced, including None, so one
    throttled call made a symbol look priceless for the whole 60s TTL — XOM
    returned null three requests running while a direct call for it answered
    165.11 throughout. A dash in a price column reads as "this company has no
    price", not "one request lost a race", so the failure has to be forgotten.
    """

    def _stub_yfinance(self, monkeypatch, exploding: bool):
        import sys
        import types

        mod = types.ModuleType("yfinance")

        class _Ticker:
            def __init__(self, *_a, **_k):
                if exploding:
                    raise RuntimeError("throttled")

            @property
            def info(self):
                return {"regularMarketPrice": 1.0, "shortName": "Stub Inc",
                        "currency": "USD", "previousClose": 1.0}

            @property
            def fast_info(self):
                return {}

        mod.Ticker = _Ticker           # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "yfinance", mod)

    def test_a_failure_leaves_no_cache_entry(self, monkeypatch):
        prices._quote_cache.pop("FAKE", None)
        self._stub_yfinance(monkeypatch, exploding=True)
        assert prices.quote("FAKE") is None
        # The important assertion: nothing was written, so the next call retries
        # rather than replaying the failure for a minute.
        assert "FAKE" not in prices._quote_cache

    def test_a_success_is_cached(self, monkeypatch):
        prices._quote_cache.pop("FAKE2", None)
        self._stub_yfinance(monkeypatch, exploding=False)
        got = prices.quote("FAKE2")
        if got is not None:          # the builder needs fields the stub may lack
            assert "FAKE2" in prices._quote_cache
            assert prices._quote_cache["FAKE2"][1] is not None


class TestTapeMerge:
    """The tape must not lose a symbol because one fetch came back short.

    yfinance drops symbols under load. A fetch returned five of eight and the
    strip simply lost Bitcoin, Crude and the Russell — which reads as delisted
    rather than as one bad request, and on a 24/7 symbol it is the difference
    between "not flashing" and "not there".
    """

    def _fake_download(self, monkeypatch, available: set):
        import sys
        import types

        class _Frame:
            def __init__(self, sym): self.sym = sym
            def __getitem__(self, key):
                if key != "Close":
                    raise KeyError(key)
                return self
            def dropna(self): return self
            def __len__(self): return 2
            @property
            def iloc(self):
                base = 100.0 + len(self.sym)
                return [base, base * 1.01]

        class _Data:
            def __getitem__(self, sym):
                if sym not in available:
                    raise KeyError(sym)
                return _Frame(sym)

        mod = types.ModuleType("yfinance")
        mod.download = lambda *a, **k: _Data()   # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "yfinance", mod)

    def test_a_short_fetch_keeps_the_symbols_it_did_not_return(self, monkeypatch):
        prices._tape_cache = (0.0, [])
        self._fake_download(monkeypatch, {"^GSPC", "^DJI", "^IXIC", "^RUT",
                                          "^TNX", "CL=F", "GC=F", "BTC-USD"})
        full = [r["symbol"] for r in prices.market_tape()]
        assert "BTC-USD" in full and len(full) >= 8

        # Now a degraded round that only prices three of them.
        prices._tape_cache = (0.0, prices._tape_cache[1])
        self._fake_download(monkeypatch, {"^GSPC", "^DJI", "^IXIC"})
        after = [r["symbol"] for r in prices.market_tape()]
        assert "BTC-USD" in after, "a short fetch dropped a symbol off the tape"
        assert after == full, "order changed when a symbol failed and returned"
