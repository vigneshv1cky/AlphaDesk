"""Tests for the signal-reversal exit tier in quant/watcher.py — the exit-side
trigger for desk/watcher.py's intraday MACD/RSI entries."""

from alphadesk.quant import watcher as qwatcher


def setup_function(_):
    qwatcher.reset()


def teardown_function(_):
    qwatcher.reset()


def test_signal_reversed_exits_before_trailing_engages():
    """A position with no profit yet (trailing not activated) still exits on
    signal_reversed=True — the reversal tier doesn't depend on P&L."""
    qwatcher.init_position(1, entry_price=100.0, current_price=100.5)
    result = qwatcher.check_exits(
        1, "LONG", entry=100.0, target=110.0, stop=95.0, current=100.5,
        signal_reversed=True)
    assert result is not None
    assert result["level"] == "signal-reverse"
    assert result["tier"] == qwatcher.EXIT_TREND_REVERSE


def test_hard_target_wins_over_signal_reversed():
    """A realized target fill is never skipped for a soft signal — target/stop
    tiers are checked before signal_reversed."""
    qwatcher.init_position(1, entry_price=100.0, current_price=100.0)
    result = qwatcher.check_exits(
        1, "LONG", entry=100.0, target=110.0, stop=95.0, current=110.0,
        signal_reversed=True)
    assert result["level"] == "target"


def test_hard_stop_wins_over_signal_reversed():
    qwatcher.init_position(1, entry_price=100.0, current_price=100.0)
    result = qwatcher.check_exits(
        1, "LONG", entry=100.0, target=110.0, stop=95.0, current=95.0,
        signal_reversed=True)
    assert result["level"] == "stop"


def test_signal_reversed_defaults_to_false():
    """Callers that don't pass signal_reversed (positions entered some other
    way, or missing indicator data upstream) are unaffected."""
    qwatcher.init_position(1, entry_price=100.0, current_price=100.5)
    result = qwatcher.check_exits(
        1, "LONG", entry=100.0, target=110.0, stop=95.0, current=100.5)
    assert result is None
