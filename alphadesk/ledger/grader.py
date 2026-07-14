"""The grader — turns picks into a scorecard. Pure code, zero judgment.

Semantics:
  • Closed-market decisions (entry_price NULL) enter at the OPEN of the first
    trading day after the decision — never at a stale prior close.
  • ret_1d = close of entry day +1 trading day; ret_horizon = close of entry
    day + horizon_days trading days. Direction-aware (SHORT inverts).
  • Benchmark: SPY over the identical window (short picks benchmark against
    short-SPY, keeping alpha symmetric).
  • alpha_net = directional return − benchmark − friction. Friction is
    2 × FRICTION_BPS_PER_SIDE (doubled again for LOW_LIQUIDITY picks).
"""

import logging
from datetime import datetime, timezone

from alphadesk.config import ET, FRICTION_BPS_PER_SIDE
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.grader")

_history_cache: dict[str, object] = {}


def _daily_history(symbol: str):
    """Daily OHLC frame for the last ~60 days (cached per grading pass)."""
    if symbol in _history_cache:
        return _history_cache[symbol]
    import yfinance as yf
    df = yf.Ticker(symbol).history(period="60d", interval="1d")
    if df is None or df.empty:
        _history_cache[symbol] = None
        return None
    df = df.tz_convert(ET) if df.index.tz is not None else df.tz_localize(ET)
    _history_cache[symbol] = df
    return df


def _entry_and_outcomes(row: dict, df, spy) -> dict | None:
    """Compute gradable fields for one pick, or None if not yet gradable."""
    import pandas as pd

    decided = datetime.fromisoformat(row["ts"])
    if decided.tzinfo is None:
        decided = decided.replace(tzinfo=timezone.utc)
    decided_et = decided.astimezone(ET)
    decided_day = pd.Timestamp(decided_et).normalize()

    days = df.index.normalize().unique()

    if row["entry_price"] is not None:
        entry_price = float(row["entry_price"])
        entry_day_candidates = days[days <= decided_day]
        if len(entry_day_candidates) == 0:
            return None
        entry_day = entry_day_candidates[-1]
    else:
        # decided while closed → enter at next trading day's open
        future = days[days > decided_day] if decided_et.hour >= 16 or row["session"] == "CLOSED" \
            else days[days >= decided_day]
        if len(future) == 0:
            return None
        entry_day = future[0]
        entry_price = float(df.loc[df.index.normalize() == entry_day, "Open"].iloc[0])

    after = days[days > entry_day]

    def _close_after(n_days: int) -> float | None:
        if len(after) < n_days:
            return None
        day = after[n_days - 1]
        return float(df.loc[df.index.normalize() == day, "Close"].iloc[0])

    sign = 1.0 if row["direction"] == "LONG" else -1.0
    out: dict = {}

    close_1d = _close_after(1)
    if close_1d is not None and entry_price:
        out["ret_1d"] = round(sign * (close_1d - entry_price) / entry_price * 100, 3)

    horizon = int(row["horizon_days"])
    close_h = _close_after(horizon)
    if close_h is None or not entry_price:
        # horizon not reached yet — partial grade only if 1d is available
        return out or None

    ret_h = sign * (close_h - entry_price) / entry_price * 100
    out["ret_horizon"] = round(ret_h, 3)

    # SPY over the identical window
    if spy is not None:
        sdays = spy.index.normalize().unique()
        s_entry_c = sdays[sdays >= entry_day]
        if len(s_entry_c) > 0:
            s_entry_day = s_entry_c[0]
            s_after = sdays[sdays > s_entry_day]
            if len(s_after) >= horizon:
                s_entry = float(spy.loc[spy.index.normalize() == s_entry_day, "Open"].iloc[0]) \
                    if row["entry_price"] is None else \
                    float(spy.loc[spy.index.normalize() == s_entry_day, "Close"].iloc[0])
                s_exit = float(spy.loc[spy.index.normalize() == s_after[horizon - 1], "Close"].iloc[0])
                spy_ret = (s_exit - s_entry) / s_entry * 100
                out["spy_ret_horizon"] = round(spy_ret, 3)
                benchmark = spy_ret if row["direction"] == "LONG" else -spy_ret
                friction = 2 * FRICTION_BPS_PER_SIDE / 100.0  # bps → %
                if row.get("low_liquidity"):
                    friction *= 2
                out["alpha_net"] = round(ret_h - benchmark - friction, 3)

    out["graded_at"] = datetime.now(timezone.utc).isoformat()
    if row["entry_price"] is None:
        out["entry_price"] = round(entry_price, 4)
    return out


def grade_due() -> int:
    """Grade all picks whose horizons have elapsed. Returns rows updated."""
    _history_cache.clear()
    due = store.due_for_grading()
    if not due:
        return 0
    spy = _daily_history("SPY")
    graded = 0
    for row in due:
        try:
            df = _daily_history(row["symbol"])
            if df is None:
                continue
            out = _entry_and_outcomes(row, df, spy)
            if not out:
                continue
            store.update_pick(row["id"], **out)
            if "graded_at" in out:
                graded += 1
                log.info(
                    "Graded #%d %s %s %dd: ret=%.2f%% alpha_net=%s",
                    row["id"], row["symbol"], row["direction"], row["horizon_days"],
                    out.get("ret_horizon", float("nan")), out.get("alpha_net"),
                )
        except Exception as exc:
            log.warning("Grading failed for #%d %s: %s", row["id"], row["symbol"], exc)
    return graded
