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
    # Every newly-seen symbol registers on the live price stream immediately —
    # the old batch scanner never did this, so get_spread() was silently None
    # for freshly-scored candidates the whole run.
    assert mock_register.call_count == 2


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


def test_tick_low_score_candidate_is_not_booked():
    """The whole quality control now rests on this threshold (no ranking
    against other candidates to fall back on) — verify a below-bar score is
    dropped, not booked, and still recorded for anti-survivorship grading."""
    watcher._watched = {"AAPL": [{"published_at": "2026-08-13T09:00:00+00:00", "tickers": ["AAPL"]}]}
    low_score = {"score": 1.0, "composite": 1.0, "direction": "LONG", "signals": {}, "_pctx": {}}

    async def fake_score(sym, arts, moves, weights):
        return low_score

    with patch.object(watcher, "MAX_OPEN_POSITIONS", 0), \
         patch.object(watcher, "DAILY_LOSS_STOP_PCT", 0), \
         patch("alphadesk.ledger.store.open_taken_picks", return_value=[]), \
         patch("alphadesk.ingest.prices.moves_since_report", return_value={}), \
         patch("alphadesk.quant.calibrate.load_weights", return_value={}), \
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
    assert any(r["symbol"] == "AAPL" and "pre-filter" in r["reason"] for r in skipped)
