"""Execution desk — pure code. ATR-based trade plans + exit physics.

Direction and horizon are FIXED upstream; this module sets entry/target/stop
from ATR math and provides level-crossing / fill-resolution logic."""

import logging

log = logging.getLogger("alphadesk.plan")


def _coherent(direction: str, entry: float, target: float, stop: float,
              last_price: float | None) -> bool:
    if min(entry, target, stop) <= 0:
        return False
    if direction == "LONG" and not (stop < entry < target):
        return False
    if direction == "SHORT" and not (target < entry < stop):
        return False
    if last_price and abs(entry - last_price) / last_price > 0.20:
        return False
    if abs(target - entry) / entry > 0.60:
        return False
    if abs(stop - entry) / entry > 0.30:
        return False
    from alphadesk.config import MIN_RISK_REWARD_RATIO, MIN_STOP_DISTANCE_PCT
    reward = abs(target - entry) / entry
    risk = abs(stop - entry) / entry
    if risk > 0 and reward / risk < MIN_RISK_REWARD_RATIO:
        return False
    if risk < MIN_STOP_DISTANCE_PCT / 100.0:
        return False
    return True


def realized_exit(direction: str, entry, exit_price, spy_then, spy_now,
                  low_liquidity: bool = False) -> dict:
    from alphadesk.config import FRICTION_BPS_PER_SIDE
    out: dict = {"exit_price": round(float(exit_price), 4) if exit_price else None,
                  "exit_return_pct": None, "exit_alpha": None}
    if not (entry and exit_price):
        return out
    sign = 1.0 if direction == "LONG" else -1.0
    ret = sign * (exit_price - entry) / entry * 100
    out["exit_return_pct"] = round(ret, 3)
    if spy_then and spy_now:
        spy_ret = sign * (spy_now - spy_then) / spy_then * 100
        friction = 2 * FRICTION_BPS_PER_SIDE / 100.0
        if low_liquidity:
            friction *= 2
        out["exit_alpha"] = round(ret - spy_ret - friction, 3)
    return out


def level_crossed(direction: str, price: float, target: float, stop: float) -> str | None:
    up = direction == "LONG"
    if (price >= target) if up else (price <= target):
        return "target"
    if (price <= stop) if up else (price >= stop):
        return "stop"
    return None


def first_touch_exit(direction: str, target: float, stop: float,
                     bars: list[dict]) -> dict | None:
    up = direction == "LONG"
    for b in bars:
        o, hi, lo = b["open"], b["high"], b["low"]
        if up:
            if o >= target:
                return {"level": "target", "price": round(o, 4)}
            if o <= stop:
                return {"level": "stop", "price": round(o, 4)}
            hit_t, hit_s = hi >= target, lo <= stop
        else:
            if o <= target:
                return {"level": "target", "price": round(o, 4)}
            if o >= stop:
                return {"level": "stop", "price": round(o, 4)}
            hit_t, hit_s = lo <= target, hi >= stop
        if hit_t and hit_s:
            return {"level": "stop", "price": round(stop, 4)}
        if hit_t:
            return {"level": "target", "price": round(target, 4)}
        if hit_s:
            return {"level": "stop", "price": round(stop, 4)}
    return None


def limit_fill(direction: str, order_type: str | None, entry: float | None,
               open_px: float | None, high_px: float | None, low_px: float | None,
               buffer_pct: float, stop: float | None = None,
               min_cushion_frac: float = 0.0) -> float | None:
    b = max(0.0, buffer_pct) / 100.0
    if order_type != "limit" or not entry or open_px is None:
        px: float | None = open_px
        if px is not None and entry:
            from alphadesk.config import ENTRY_GAP_SKIP_PCT
            if ENTRY_GAP_SKIP_PCT > 0 and abs(px - entry) / entry > ENTRY_GAP_SKIP_PCT / 100.0:
                return None
    elif direction == "LONG":
        if open_px <= entry:
            px = round(open_px, 4)
        elif low_px is not None and low_px <= entry * (1 + b):
            px = round(entry if low_px <= entry else low_px, 4)
        else:
            px = None
    elif open_px >= entry:
        px = round(open_px, 4)
    elif high_px is not None and high_px >= entry * (1 - b):
        px = round(entry if high_px >= entry else high_px, 4)
    else:
        px = None
    if px is None:
        return None
    if order_type == "limit" and stop and entry and min_cushion_frac > 0:
        planned = abs(entry - stop)
        cushion = (stop - px) if direction == "SHORT" else (px - stop)
        if planned > 0 and cushion < min_cushion_frac * planned:
            return None
    return px


def atr_plan(symbol: str, direction: str, horizon_days: int,
             last_price: float, atr_pct: float | None = None) -> dict | None:
    if not last_price or last_price <= 0:
        return None
    from alphadesk.config import PLAN_TARGET_ATR, PLAN_STOP_ATR
    atr = atr_pct or 2.0
    atr = max(atr, 0.5)
    atr_dec = atr / 100.0
    tgt_mult = PLAN_TARGET_ATR
    sl_mult = PLAN_STOP_ATR

    if direction == "LONG":
        target = round(last_price * (1 + atr_dec * tgt_mult), 4)
        stop = round(last_price * (1 - atr_dec * sl_mult), 4)
        min_dist = 0.10 if last_price < 10 else 0.25
        if (last_price - stop) < min_dist:
            stop = round(last_price - min_dist, 4)
    else:
        target = round(last_price * (1 - atr_dec * tgt_mult), 4)
        stop = round(last_price * (1 + atr_dec * sl_mult), 4)
        min_dist = 0.10 if last_price < 10 else 0.25
        if (stop - last_price) < min_dist:
            stop = round(last_price + min_dist, 4)

    if horizon_days >= 3:
        target_mult = min(horizon_days * 1.5, 6.0)
        target = round(last_price * (1 + atr_dec * target_mult), 4) if direction == "LONG" \
                 else round(last_price * (1 - atr_dec * target_mult), 4)

    if not _coherent(direction, last_price, target, stop, last_price):
        return None

    hold = "single-day" if horizon_days <= 1 else "multi-day"
    return {"entry": round(last_price, 4), "target": target, "stop": stop,
            "note": f"{direction} {symbol} ATR={atr:.1f}% tgt={round(atr*tgt_mult)}% sl={round(atr*sl_mult)}%",
            "hold": hold, "order": "market",
            "target_atr_mult": tgt_mult, "stop_atr_mult": sl_mult}
