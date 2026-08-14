"""Tiered exit engine — pure math, zero LLM. Priority-ordered exit triggers
for open positions. Reads live prices from quant.stream and closes at the
FIRST condition actually hit.

Exit tiers (priority order):
  1. Hard take-profit — price reaches plan target
  2. Trailing stop — activates after profit exceeds activation threshold,
     then trails at offset
  3. Hard stop-loss — price hits plan stop
  4. Spike reversal — unusual volatility spike that reverses (blow-off top /
     capitulation bottom)
  5. Stale exit — no significant movement hours after entry
  7. MA reconvergence — the price/SMA-50 gap that justified entry (desk/watcher.py's
     technical-setup engine) is closing back up; checked after hard
     target/stop (never skip a realized fill for a soft signal) but before
     trailing/give-back/spike (a "thesis invalidated" signal is more decisive
     than those heuristics). Fails open — missing data or a non-technical-
     setup position just means this tier never fires.
"""

import logging
import os
import time
from typing import Optional

log = logging.getLogger("alphadesk.quant.watcher")

EXIT_TP = 1
EXIT_TRAIL = 2
EXIT_STOP = 3
EXIT_SPIKE = 4
EXIT_STALE = 5
EXIT_CLOSE = 6
EXIT_MA_CONVERGE = 7

EXIT_LABELS = {
    EXIT_TP: "take-profit",
    EXIT_TRAIL: "trailing-stop",
    EXIT_STOP: "stop-loss",
    EXIT_SPIKE: "spike-reversal",
    EXIT_STALE: "stale-expiry",
    EXIT_CLOSE: "session-close",
    EXIT_MA_CONVERGE: "ma-reconverge",
}

# ── Configurable thresholds (env-overridable, self-optimizing) ────────────────

TRAIL_ACTIVATION_PCT = float(os.environ.get("TRAIL_ACTIVATION", "1.5"))
TRAIL_OFFSET_PCT = 0.5
TRAIL_OFFSET_ATR_FRAC = float(os.environ.get("TRAIL_OFFSET_ATR", "0.15"))
TRAIL_OFFSET_MIN = 0.0025
TRAIL_OFFSET_MAX = float(os.environ.get("TRAIL_OFFSET_MAX", "0.02"))
PROFIT_PEAK_THRESHOLD = 3.0
GIVEBACK_RETAIN_FRAC = float(os.environ.get("GIVEBACK_RETAIN", "0.4"))
GIVEBACK_ABSOLUTE_FLOOR = float(os.environ.get("GIVEBACK_FLOOR", "1.0"))
SPIKE_VOLATILITY_MULT = 3.0
STALE_HOURS = 6
STALE_MIN_MOVE_PCT = 0.5

# Session-scoped model: every position exits at the close of the session it was
# booked in — no carry-over across markets. Each tradeable window is its own
# trade: PRE (4:00–9:30), OPEN (9:30–16:00), AFTER (16:00–20:00). The EXIT buffer
# is centralized in config (SESSION_EXIT_MIN) — positions exit a few minutes
# before the boundary so the close clears the session.
from alphadesk.config import SESSION_EXIT_MIN

SESSION_CLOSE_TIMES = SESSION_EXIT_MIN


def session_close_due() -> tuple[str, int] | None:
    """(session, close_minute) if the current session is at/after its close time,
    else None. Shared by the quant watcher and the main position watcher so both
    enforce the same no-carry-over exit."""
    from alphadesk.config import session as _market_session, now_et as _now_et
    sess = _market_session()
    close_min = SESSION_CLOSE_TIMES.get(sess)
    if close_min is None:
        return None
    now = _now_et()
    minutes = now.hour * 60 + now.minute
    if minutes >= close_min:
        return sess, close_min
    return None

# Per-pick tracking state
_trail_peaks: dict[int, float] = {}
_entry_timestamps: dict[int, float] = {}
_pick_history: dict[int, list[tuple[float, float]]] = {}  # pick_id → [(ts, price), ...]
_atr_map: dict[int, float] = {}
_best_peaks: dict[int, float] = {}  # best profit % ever seen — for give-back detection


def _compute_volatility(prices: list[float]) -> float:
    """Standard deviation of % returns over the last N prices."""
    if len(prices) < 5:
        return 0.0
    rets = [(prices[i] - prices[i - 1]) / prices[i - 1] * 100
            for i in range(1, len(prices))]
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return var ** 0.5


def _is_spike(prices: list[float], normal_vol: float) -> bool:
    """Check if the last return is >SPIKE_VOLATILITY_MULT × normal volatility."""
    if len(prices) < 3 or normal_vol <= 0:
        return False
    last_ret = abs((prices[-1] - prices[-2]) / prices[-2] * 100)
    return last_ret > normal_vol * SPIKE_VOLATILITY_MULT


def init_position(pick_id: int, entry_price: float, current_price: float,
                  atr_pct: float | None = None):
    """Initialize tracking for a new position."""
    _trail_peaks[pick_id] = current_price
    _entry_timestamps[pick_id] = time.time()
    _pick_history[pick_id] = [(time.time(), current_price)]
    _best_peaks[pick_id] = 0.0
    if atr_pct is not None and atr_pct > 0:
        _atr_map[pick_id] = atr_pct


def set_atr(pick_id: int, atr_pct: float | None):
    """Set the ATR estimate used to size the trailing-stop offset, if not
    already known. Unlike init_position, this doesn't touch trail/peak state —
    safe to call every tick for positions that were never explicitly
    initialized (e.g. the quant watch loop, which discovers live picks by
    polling the ledger rather than an explicit open-position event)."""
    if atr_pct is not None and atr_pct > 0 and pick_id not in _atr_map:
        _atr_map[pick_id] = atr_pct


def update_price(pick_id: int, price: float):
    """Feed a new price tick to the watcher."""
    if pick_id not in _pick_history:
        _pick_history[pick_id] = [(time.time(), price)]
        _trail_peaks[pick_id] = price
        if pick_id not in _entry_timestamps:
            _entry_timestamps[pick_id] = time.time()
    else:
        _pick_history[pick_id].append((time.time(), price))
        if len(_pick_history[pick_id]) > 60:
            _pick_history[pick_id] = _pick_history[pick_id][-60:]


def check_exits(pick_id: int, direction: str, entry: float,
                target: float, stop: float, current: float,
                ma_converging: bool = False) -> Optional[dict]:
    """Check all exit tiers for a position. Returns the FIRST exit triggered,
    or None if no exit condition is met.

    ma_converging: True if the price/SMA-50 gap that justified a technical-setup
    entry (desk/watcher.py) is closing back up — the trend is fading, exit
    regardless of P&L. Fails open by default (False) so positions entered
    some other way, or with missing indicator data, are unaffected.

    Returns {level, price, reason} or None.
    """
    up = direction == "LONG"
    ptr = current

    # Tier 1: hard take-profit
    hit_tp = ptr >= target if up else ptr <= target
    if hit_tp:
        return {"level": "target", "price": round(target, 4),
                "reason": "take-profit hit", "tier": EXIT_TP}

    # Tier 3: hard stop-loss
    hit_stop = ptr <= stop if up else ptr >= stop
    if hit_stop:
        return {"level": "stop", "price": round(stop, 4),
                "reason": "stop-loss hit", "tier": EXIT_STOP}

    # Tier 7: MA reconvergence — checked after hard target/stop (never skip a
    # realized fill for a soft signal) but before trailing/give-back/spike (a
    # "thesis invalidated" signal is more decisive than those heuristics).
    if ma_converging:
        return {"level": "ma-reconverge", "price": round(ptr, 4),
                "reason": "MA reconverging — trend invalidated", "tier": EXIT_MA_CONVERGE}

    # Tier 2: trailing stop (only if activated by profit)
    peak = _trail_peaks.get(pick_id, entry)
    if up and ptr > peak:
        _trail_peaks[pick_id] = ptr
        peak = ptr
    elif not up and ptr < peak:
        _trail_peaks[pick_id] = ptr
        peak = ptr

    profit_pct = ((peak - entry) / entry * 100) if up else ((entry - peak) / entry * 100)
    if profit_pct >= TRAIL_ACTIVATION_PCT:
        atr = _atr_map.get(pick_id)
        if atr:
            offset = max(TRAIL_OFFSET_MIN, min(atr * TRAIL_OFFSET_ATR_FRAC, TRAIL_OFFSET_MAX))
        else:
            offset = TRAIL_OFFSET_PCT / 100
        trail_level = peak * (1 - offset) if up else peak * (1 + offset)
        hit_trail = ptr <= trail_level if up else ptr >= trail_level
        if hit_trail:
            return {"level": "trailing-stop", "price": round(trail_level, 4),
                    "reason": f"trailing stop @ {round(offset*100,2)}% off peak",
                    "tier": EXIT_TRAIL}

    # Proportional profit give-back: peak was >threshold, now dropped to <40% of peak
    best_pct = _best_peaks.get(pick_id, 0.0)
    current_pnl = ((ptr - entry) / entry * 100) if up else ((entry - ptr) / entry * 100)
    if best_pct < current_pnl:
        _best_peaks[pick_id] = current_pnl
        best_pct = current_pnl
    if best_pct >= PROFIT_PEAK_THRESHOLD:
        floor = max(best_pct * GIVEBACK_RETAIN_FRAC, GIVEBACK_ABSOLUTE_FLOOR)
        if current_pnl < floor:
            return {"level": "give-back", "price": round(ptr, 4),
                    "reason": f"give-back: was +{best_pct:.1f}%, now +{current_pnl:.1f}% (floor {floor:.1f}%)",
                    "tier": EXIT_SPIKE}

    # Tier 4: spike reversal
    prices = [p for _, p in _pick_history.get(pick_id, [])]
    if len(prices) >= 10:
        normal_vol = _compute_volatility(prices[:-1])
        if _is_spike(prices, normal_vol):
            prev = prices[-2]
            spike_dir = (prices[-1] - prev) / prev * 100
            opposite = (spike_dir > 0 and direction == "SHORT") or \
                       (spike_dir < 0 and direction == "LONG")
            if opposite and abs(spike_dir) > 0.5:
                return {"level": "spike", "price": round(ptr, 4),
                        "reason": f"spike reversal ({spike_dir:.1f}%)",
                        "tier": EXIT_SPIKE}

    # Tier 5: session-close exit — session-scoped model (no carry-over across
    # markets): every position exits at the close of the session it's in.
    due = session_close_due()
    if due:
        return {"level": "session-close", "price": round(ptr, 4),
                "reason": f"session-close exit ({due[0]})", "tier": EXIT_CLOSE}

    # Tier 6: stale exit — only during market hours (prices don't move overnight)
    entry_ts = _entry_timestamps.get(pick_id)
    if entry_ts and (time.time() - entry_ts) > STALE_HOURS * 3600:
        from alphadesk.config import session as _market_session
        if _market_session() != "CLOSED":
            move = abs((ptr - entry) / entry * 100)
            if move < STALE_MIN_MOVE_PCT:
                return {"level": "stale", "price": round(ptr, 4),
                        "reason": f"stale after {STALE_HOURS}h, {move:.1f}% move",
                        "tier": EXIT_STALE}

    return None


def clear_position(pick_id: int):
    """Remove tracking state for a closed position."""
    _trail_peaks.pop(pick_id, None)
    _entry_timestamps.pop(pick_id, None)
    _pick_history.pop(pick_id, None)
    _atr_map.pop(pick_id, None)
    _best_peaks.pop(pick_id, None)


def reset():
    """Reset all tracking state."""
    _trail_peaks.clear()
    _entry_timestamps.clear()
    _pick_history.clear()
    _atr_map.clear()
    _best_peaks.clear()
