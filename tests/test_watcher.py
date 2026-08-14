"""Tests for the continuous entry watcher (desk/watcher.py), which replaced
the batch scanner (desk/stream.py, removed 2026-08-13)."""

import asyncio
from unittest.mock import patch

from alphadesk.desk import watcher


def setup_function(_):
    watcher.clear_pool()


def teardown_function(_):
    watcher.clear_pool()


# ── clear_pool / watched_symbols ─────────────────────────────────────────────

def test_clear_pool_resets_all_state():
    watcher._watched["AAPL"] = [{}]
    watcher._booked_today_count = 5
    watcher.clear_pool()
    assert watcher.watched_symbols() == []
    assert watcher._booked_today_count == 0
    assert watcher._booked_today_date is None


# ── refresh_pool: candidate pool membership ─────────────────────────────────

def test_refresh_pool_populates_from_candidates():
    candidates = {"AAPL": [{"low_liquidity": False}], "TSLA": [{"low_liquidity": False}]}
    with patch("alphadesk.ingest.earnings.drift_candidates", return_value=dict(candidates)), \
         patch("alphadesk.ledger.store.open_taken_picks", return_value=[]), \
         patch("alphadesk.quant.stream.register") as mock_register:
        watcher.refresh_pool()
    assert set(watcher.watched_symbols()) == {"AAPL", "TSLA"}
    # Watched candidates do NOT register on the live price stream — that
    # 30-symbol budget (Basic data plan) is reserved for SPY + open positions,
    # which the exit-side watcher depends on. get_spread() stays None for
    # watched candidates, same as before the (reverted) eager-registration change.
    mock_register.assert_not_called()


def test_refresh_pool_anti_double_dip_excludes_held_symbol():
    candidates = {"AAPL": [{"low_liquidity": False}], "TSLA": [{"low_liquidity": False}]}
    with patch("alphadesk.ingest.earnings.drift_candidates", return_value=dict(candidates)), \
         patch("alphadesk.ledger.store.open_taken_picks", return_value=[{"symbol": "AAPL"}]), \
         patch("alphadesk.quant.stream.register"):
        watcher.refresh_pool()
    assert "AAPL" not in watcher.watched_symbols()
    assert "TSLA" in watcher.watched_symbols()


def test_refresh_pool_liquidity_prefilter_excludes_illiquid():
    candidates = {"AAPL": [{"low_liquidity": True}], "TSLA": [{"low_liquidity": False}]}
    with patch("alphadesk.ingest.earnings.drift_candidates", return_value=dict(candidates)), \
         patch("alphadesk.ledger.store.open_taken_picks", return_value=[]), \
         patch("alphadesk.quant.stream.register"):
        watcher.refresh_pool()
    assert "AAPL" not in watcher.watched_symbols()
    assert "TSLA" in watcher.watched_symbols()


def test_refresh_pool_keeps_unarmed_liquidity_candidate():
    """low_liquidity absent/None (never armed by the 6h earnings loop) fails
    open — a fresh candidate isn't dropped just because we don't know yet."""
    candidates = {"AAPL": [{}]}
    with patch("alphadesk.ingest.earnings.drift_candidates", return_value=dict(candidates)), \
         patch("alphadesk.ledger.store.open_taken_picks", return_value=[]), \
         patch("alphadesk.quant.stream.register"):
        watcher.refresh_pool()
    assert "AAPL" in watcher.watched_symbols()


def test_refresh_pool_drops_symbols_no_longer_candidates():
    watcher._watched = {"OLD": [{}]}
    with patch("alphadesk.ingest.earnings.drift_candidates", return_value={"NEW": [{"low_liquidity": False}]}), \
         patch("alphadesk.ledger.store.open_taken_picks", return_value=[]), \
         patch("alphadesk.quant.stream.register"):
        watcher.refresh_pool()
    assert watcher.watched_symbols() == ["NEW"]


# ── daily safety-cap counter ────────────────────────────────────────────────

def test_reset_daily_count_fires_on_new_day():
    watcher._booked_today_count = 7
    watcher._booked_today_date = None
    watcher._reset_daily_count_if_new_day()
    assert watcher._booked_today_count == 0
    assert watcher._booked_today_date is not None


def test_reset_daily_count_preserves_same_day_count():
    watcher._reset_daily_count_if_new_day()
    watcher._booked_today_count = 3
    watcher._reset_daily_count_if_new_day()
    assert watcher._booked_today_count == 3


# ── tick(): pool-level behavior without touching the network ───────────────

def test_tick_empty_pool_returns_immediately():
    assert watcher.watched_symbols() == []
    assert asyncio.run(watcher.tick()) == []


def test_tick_respects_daily_safety_cap():
    watcher._watched = {"AAPL": [{}]}
    watcher._reset_daily_count_if_new_day()
    watcher._booked_today_count = watcher.MAX_ENTRIES_PER_DAY
    assert asyncio.run(watcher.tick()) == []


def test_tick_respects_max_open_positions_rail():
    watcher._watched = {"AAPL": [{}]}
    with patch.object(watcher, "MAX_OPEN_POSITIONS", 5), \
         patch("alphadesk.ledger.store.open_position_count", return_value=5):
        assert asyncio.run(watcher.tick()) == []


def test_tick_respects_daily_loss_stop_rail():
    watcher._watched = {"AAPL": [{}]}
    with patch.object(watcher, "DAILY_LOSS_STOP_PCT", 10.0), \
         patch("alphadesk.ledger.store.today_realized_pnl_pct", return_value=-15.0), \
         patch("alphadesk.app.alerts.notify"):
        assert asyncio.run(watcher.tick()) == []


def test_tick_dropped_candidate_is_not_booked():
    """The whole quality control now rests on _entry_signal's boolean gate (no
    ranking against other candidates to fall back on) — verify a dropped
    candidate isn't booked, and still recorded for anti-survivorship grading."""
    watcher._watched = {"AAPL": [{"published_at": "2026-08-13T09:00:00+00:00", "tickers": ["AAPL"]}]}

    async def fake_score(sym, arts):
        return None, "no fresh cross, no qualifying reentry"

    with patch.object(watcher, "MAX_OPEN_POSITIONS", 0), \
         patch.object(watcher, "DAILY_LOSS_STOP_PCT", 0), \
         patch("alphadesk.ledger.store.open_taken_picks", return_value=[]), \
         patch.object(watcher, "score_candidate", side_effect=fake_score), \
         patch("alphadesk.ledger.store.funnel_add") as mock_funnel, \
         patch("alphadesk.ledger.store.record_skips") as mock_skips, \
         patch("alphadesk.ledger.store.record_pick") as mock_record:
        result = asyncio.run(watcher.tick())

    assert result == []
    mock_record.assert_not_called()
    mock_funnel.assert_called_once()
    mock_skips.assert_called_once()
    skipped = mock_skips.call_args[0][0]
    assert any(r["symbol"] == "AAPL" and "no fresh cross" in r["reason"] for r in skipped)


# ── _entry_signal: the MA-convergence rule engine ───────────────────────────

def _pctx(gap=3.0, gap_prior=1.0, rsi=60.0, rvol=1.5, days_since_cross=1):
    """A pctx fixture that passes every gate by default (fresh-cross LONG) —
    each test overrides just the field(s) it's probing."""
    return {"ma_gap_pct": gap, "ma_gap_pct_3d_ago": gap_prior, "rsi_9": rsi,
            "rvol": rvol, "days_since_ma_cross": days_since_cross}


def test_entry_signal_fresh_cross_long_passes():
    setup, reason = watcher._entry_signal("AAPL", _pctx())
    assert reason is None
    assert setup["direction"] == "LONG"
    assert setup["entry_mode"] == "fresh_cross"


def test_entry_signal_fresh_cross_short_passes():
    setup, reason = watcher._entry_signal(
        "AAPL", _pctx(gap=-3.0, gap_prior=-1.0, rsi=40.0))
    assert reason is None
    assert setup["direction"] == "SHORT"
    assert setup["entry_mode"] == "fresh_cross"


def test_entry_signal_missing_data_fails_closed():
    setup, reason = watcher._entry_signal("AAPL", _pctx(gap=None))
    assert setup is None
    assert "insufficient" in reason


def test_entry_signal_converging_blocks_entry():
    """Gap narrowing vs. 3 days ago (was 3.0%, now 1.0%) signals an imminent
    reversal — blocked even though direction/RSI/rvol/fresh-cross all pass."""
    setup, reason = watcher._entry_signal(
        "AAPL", _pctx(gap=1.0, gap_prior=3.0))
    assert setup is None
    assert "converging" in reason


def test_entry_signal_rsi_out_of_band_rejects():
    setup, reason = watcher._entry_signal("AAPL", _pctx(rsi=85.0))
    assert setup is None
    assert "RSI" in reason


def test_entry_signal_low_rvol_rejects():
    setup, reason = watcher._entry_signal("AAPL", _pctx(rvol=0.5))
    assert setup is None
    assert "rvol" in reason


def test_entry_signal_no_fresh_cross_no_reentry_rejects():
    setup, reason = watcher._entry_signal(
        "AAPL", _pctx(days_since_cross=10))
    assert setup is None
    assert "no fresh cross" in reason


def test_entry_signal_reentry_without_fresh_cross_passes():
    """After a prior exit at gap=2.0%, a later tick with gap=3.0% (further
    from the MA, same direction) qualifies as a reentry — no fresh cross
    needed, unlike the initial entry."""
    watcher._reentry_state[("AAPL", "LONG")] = 2.0
    setup, reason = watcher._entry_signal(
        "AAPL", _pctx(gap=3.0, gap_prior=1.0, days_since_cross=10))
    assert reason is None
    assert setup["entry_mode"] == "reentry"


def test_entry_signal_reentry_needs_greater_distance():
    """gap=1.5% does NOT exceed the 2.0% distance recorded at the prior
    exit — no qualifying reentry."""
    watcher._reentry_state[("AAPL", "LONG")] = 2.0
    setup, reason = watcher._entry_signal(
        "AAPL", _pctx(gap=1.5, gap_prior=1.0, days_since_cross=10))
    assert setup is None
    assert "no fresh cross" in reason


def test_entry_signal_per_symbol_reentry_cap():
    """MAX_REENTRIES_PER_SYMBOL_PER_DAY total bookings (fresh + reentries)
    per symbol+direction, independent of the global daily cap."""
    watcher._bookings_today[("AAPL", "LONG")] = watcher.MAX_REENTRIES_PER_SYMBOL_PER_DAY
    setup, reason = watcher._entry_signal("AAPL", _pctx())
    assert setup is None
    assert "cap" in reason


# ── record_exit_distance / ma_trend_status ──────────────────────────────────

def test_record_exit_distance_stores_absolute_value():
    watcher.record_exit_distance("aapl", "SHORT", -2.5)
    assert watcher._reentry_state[("AAPL", "SHORT")] == 2.5


def test_ma_trend_status_converging_true():
    assert watcher.ma_trend_status(_pctx(gap=1.0, gap_prior=3.0))["converging"] is True


def test_ma_trend_status_converging_false():
    assert watcher.ma_trend_status(_pctx(gap=3.0, gap_prior=1.0))["converging"] is False


def test_ma_trend_status_fails_open_on_missing_data():
    """Unlike _entry_signal (fails closed), the exit-side check fails open —
    missing data should never force an exit."""
    assert watcher.ma_trend_status({})["converging"] is False
