"""Intraday RSI backtest — replays desk/watcher.py's LIVE entry engine over
historical 1-minute bars. Separate from ledger/backtest.py, which is daily-bar
post-earnings-drift research and structurally cannot test an intraday crossing.

Fidelity notes (read these before trusting a number):

  • Entry/exit thresholds, the rvol/ATR floors, the per-symbol daily booking
    cap and the target/stop math are the LIVE ones — this module imports
    config and calls desk/plan.py's atr_plan() rather than reimplementing
    them, so the backtest can't silently drift from production.
  • RSI-9 is computed with the same EWM math as ingest/prices.py. Live
    recomputes it over a rolling 5-day window; here it's computed once over
    the whole series. With adjust=False the seed washes out within a few
    hundred bars, so the two agree except at the very start of a symbol.
  • rvol / atr_pct are reconstructed from DAILY bars using strictly
    prior-day data. Live's ATR includes the in-progress partial bar, so live
    is very slightly forward-looking where this is not. Deliberate: a
    backtest must not peek.
  • DATA QUALITY IS THE DOMINANT ISSUE. Alpaca's free IEX feed carries only
    a few percent of consolidated volume, so illiquid symbols have no print
    in most minutes. `min_coverage` filters on measured bars-per-session;
    run it at 0.0 to measure the engine as actually deployed, and high
    (e.g. 0.8) to ask whether the signal works where the data is real.

Exit tiers reproduced: target, backstop stop, RSI signal-reversal, trailing
stop, proportional give-back, session close. NOT reproduced:
  • spike reversal — defined on 5-second tick microstructure (quant/watcher.py
    feeds it a 60-sample history at 5s cadence); minute bars cannot express it.
  • stale expiry — needs a >6h hold, impossible in an OPEN-only session whose
    max hold is 9:45→15:45.
Within a single bar, if the range spans BOTH target and stop, OHLC cannot say
which printed first; this resolves to the STOP (pessimistic).
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from alphadesk.config import (
    ENTRY_BUFFER_MIN,
    EXIT_BUFFER_MIN,
    LOW_LIQUIDITY_DOLLAR_VOL,
    MA_ENTRY_MIN_ATR_PCT,
    MA_ENTRY_MIN_RVOL,
    MA_STOP_BACKSTOP_ATR,
    MAX_BOOKINGS_PER_SYMBOL_PER_DAY,
    PLAN_TARGET_ATR,
    RSI_CROSS_OVERBOUGHT,
    RSI_CROSS_OVERSOLD,
    START_BUFFER_MIN,
    pinned_horizon,
)
from alphadesk.desk import plan

log = logging.getLogger("alphadesk.rsi_backtest")

ET = ZoneInfo("America/New_York")

# OPEN-session entry/exit windows in ET minutes-from-midnight. Mirrors
# config's START_BUFFER_MIN / ENTRY_BUFFER_MIN / EXIT_BUFFER_MIN applied to
# the 9:30–16:00 OPEN session (the only session the live engine trades).
OPEN_MIN = 9 * 60 + 30
CLOSE_MIN = 16 * 60
ENTRY_FROM = OPEN_MIN + START_BUFFER_MIN        # 9:45
ENTRY_UNTIL = CLOSE_MIN - ENTRY_BUFFER_MIN      # 15:00
FORCE_EXIT = CLOSE_MIN - EXIT_BUFFER_MIN        # 15:45

# Trailing / give-back constants — mirrored from quant/watcher.py.
TRAIL_ACTIVATION_PCT = 1.5
TRAIL_OFFSET_ATR_FRAC = 0.15
TRAIL_OFFSET_MIN = 0.0025
TRAIL_OFFSET_MAX = 0.02
PROFIT_PEAK_THRESHOLD = 3.0
GIVEBACK_RETAIN_FRAC = 0.4
GIVEBACK_ABSOLUTE_FLOOR = 1.0

MIN_BARS = 30   # matches ingest/prices.py's history floor


# ── data ─────────────────────────────────────────────────────────────────────

def _daily_bars(symbol: str, start) -> list[dict]:
    """Daily OHLCV via Alpaca, for the rvol / ATR% / liquidity reconstruction."""
    from alphadesk.ingest.prices import _alpaca_data_client
    client = _alpaca_data_client()
    if client is None:
        return []
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        resp = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbol.upper(), timeframe=TimeFrame.Day,
            start=start, feed=DataFeed.IEX))
        data = resp.data.get(symbol.upper(), []) if hasattr(resp, "data") else []
        bars = [{"date": b.timestamp.astimezone(ET).date(), "open": float(b.open),
                 "high": float(b.high), "low": float(b.low), "close": float(b.close),
                 "volume": float(b.volume)} for b in data]
        bars.sort(key=lambda x: x["date"])
        return bars
    except Exception as exc:
        log.debug("daily_bars failed %s: %s", symbol, exc)
        return []


def _daily_context(daily: list[dict]) -> dict:
    """date → {rvol, atr_pct, avg_dollar_vol} using STRICTLY prior sessions.

    Mirrors ingest/prices.py's get_context(): rvol is the last completed
    session's volume over the mean of the 20 sessions before it; atr_pct is
    the 14-session mean true range as a % of price.
    """
    out: dict = {}
    for i in range(1, len(daily)):
        prior = daily[:i]              # everything strictly before day i
        if len(prior) < 15:
            continue
        ref = prior[-1]
        base = prior[max(0, len(prior) - 21):len(prior) - 1]
        base_vol = sum(b["volume"] for b in base) / len(base) if base else 0.0
        rvol = round(ref["volume"] / base_vol, 2) if base_vol else None

        trs = []
        for j in range(len(prior) - 14, len(prior)):
            if j <= 0:
                continue
            hi, lo, pc = prior[j]["high"], prior[j]["low"], prior[j - 1]["close"]
            trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
        last = ref["close"]
        atr_pct = round(sum(trs) / len(trs) / last * 100, 2) if trs and last else None

        adv = [b["close"] * b["volume"] for b in prior[-20:]]
        avg_dollar_vol = sum(adv) / len(adv) if adv else 0.0

        out[daily[i]["date"]] = {"rvol": rvol, "atr_pct": atr_pct,
                                 "avg_dollar_vol": avg_dollar_vol}
    return out


def _rsi_series(closes: list[float]) -> list[float]:
    """RSI-9, identical math to ingest/prices.py's get_intraday_ma_context()."""
    import pandas as pd
    s = pd.Series(closes)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 9, min_periods=9, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 9, min_periods=9, adjust=False).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0, other=float("nan"))
    rsi = (100 - 100 / (1 + rs)).where(avg_loss != 0, other=100.0)
    return [float(v) for v in rsi]


# ── replay ───────────────────────────────────────────────────────────────────

def _live_plan(sym: str, direction: str, px: float, atr_pct: float | None) -> dict | None:
    """Reproduce desk/watcher.py's _book() pricing, INCLUDING the fact that
    plan.atr_plan() never succeeds here.

    atr_plan applies _coherent(), which enforces MIN_RISK_REWARD_RATIO (1.5).
    The live engine asks for a target of PLAN_TARGET_ATR (2.0) and a stop of
    MA_STOP_BACKSTOP_ATR (4.0) — a reward/risk of 0.5 — so _coherent always
    fails and atr_plan always returns None. Every live entry is therefore
    priced by _book()'s manual fallback branch, which skips the min_dist
    floor and MIN_STOP_DISTANCE_PCT. Mirrored deliberately: the backtest must
    measure the code that runs, not the code that looks like it runs.
    """
    tp = plan.atr_plan(sym, direction, pinned_horizon("MOMENTUM"), px, atr_pct,
                       stop_atr_mult=MA_STOP_BACKSTOP_ATR)
    if tp:
        return tp
    atr = atr_pct or 2.0
    if direction == "LONG":
        return {"entry": round(px, 4),
                "target": round(px * (1 + atr / 100 * PLAN_TARGET_ATR), 4),
                "stop": round(px * (1 - atr / 100 * MA_STOP_BACKSTOP_ATR), 4)}
    return {"entry": round(px, 4),
            "target": round(px * (1 - atr / 100 * PLAN_TARGET_ATR), 4),
            "stop": round(px * (1 + atr / 100 * MA_STOP_BACKSTOP_ATR), 4)}


def _trail_offset(atr_pct: float | None) -> float:
    """Mirrors quant/watcher.py exactly, unit quirk included: it multiplies
    atr_pct (a PERCENT, e.g. 2.0) by 0.15 and then uses the result as a
    FRACTION. Since MA_ENTRY_MIN_ATR_PCT is 1.5, every live position has
    atr_pct >= 1.5, so 1.5*0.15 = 0.225 always exceeds TRAIL_OFFSET_MAX and
    the offset is pinned at 2%. Reproduced rather than corrected — the point
    is to measure what runs, not what was intended."""
    if atr_pct:
        return max(TRAIL_OFFSET_MIN, min(atr_pct * TRAIL_OFFSET_ATR_FRAC, TRAIL_OFFSET_MAX))
    return 0.005


def replay_symbol(sym: str, minute: list[dict], dctx: dict) -> list[dict]:
    """Walk one symbol's minute bars, booking and closing trades exactly as
    the live engine would. Returns closed trades."""
    if len(minute) < MIN_BARS + 2:
        return []
    closes = [b["close"] for b in minute]
    rsi = _rsi_series(closes)

    trades: list[dict] = []
    pos: dict | None = None
    bookings: dict = defaultdict(int)
    cur_day = None

    for i in range(MIN_BARS, len(minute)):
        bar = minute[i]
        t = bar["ts"].astimezone(ET)
        day = t.date()
        if day != cur_day:
            cur_day, bookings = day, defaultdict(int)
        mins = t.hour * 60 + t.minute
        if mins < OPEN_MIN or mins >= CLOSE_MIN:
            continue                                    # OPEN session only

        r_now, r_prev = rsi[i], rsi[i - 1]
        have_rsi = math.isfinite(r_now) and math.isfinite(r_prev)

        # ── exits first (a bar can't open and close the same position) ──
        if pos is not None:
            ex = None
            hi, lo = bar["high"], bar["low"]
            up = pos["direction"] == "LONG"
            # tier 3 stop checked before tier 1 target: within one bar OHLC
            # can't order them, so resolve pessimistically.
            if (up and lo <= pos["stop"]) or (not up and hi >= pos["stop"]):
                ex = ("stop", pos["stop"])
            elif (up and hi >= pos["target"]) or (not up and lo <= pos["target"]):
                ex = ("target", pos["target"])
            elif have_rsi and ((up and r_prev < RSI_CROSS_OVERBOUGHT <= r_now)
                               or (not up and r_prev > RSI_CROSS_OVERSOLD >= r_now)):
                ex = ("signal-reverse", bar["close"])
            else:
                px = bar["close"]
                # Trailing stop off the best PRICE seen, as quant/watcher.py
                # does (peak price, pulled back by `offset`), not off peak P&L.
                pos["peak_px"] = max(pos["peak_px"], px) if up else min(pos["peak_px"], px)
                peak_profit = ((pos["peak_px"] - pos["entry"]) / pos["entry"] * 100) if up \
                    else ((pos["entry"] - pos["peak_px"]) / pos["entry"] * 100)
                if peak_profit >= TRAIL_ACTIVATION_PCT:
                    off = _trail_offset(pos["atr_pct"])
                    level = pos["peak_px"] * (1 - off) if up else pos["peak_px"] * (1 + off)
                    if (up and px <= level) or (not up and px >= level):
                        ex = ("trailing-stop", level)
                pnl = ((px - pos["entry"]) / pos["entry"] * 100) if up \
                    else ((pos["entry"] - px) / pos["entry"] * 100)
                pos["peak"] = max(pos["peak"], pnl)
                if ex is None and pos["peak"] >= PROFIT_PEAK_THRESHOLD:
                    floor = max(pos["peak"] * GIVEBACK_RETAIN_FRAC, GIVEBACK_ABSOLUTE_FLOOR)
                    if pnl < floor:
                        ex = ("give-back", px)
            if ex is None and mins >= FORCE_EXIT:
                ex = ("session-close", bar["close"])
            if ex:
                pos.update(exit_reason=ex[0], exit_price=ex[1], exit_ts=bar["ts"])
                trades.append(pos)
                pos = None
            continue

        # ── entry ──
        if not (ENTRY_FROM <= mins < ENTRY_UNTIL) or not have_rsi:
            continue
        cross_long = r_prev <= RSI_CROSS_OVERSOLD < r_now
        cross_short = r_prev >= RSI_CROSS_OVERBOUGHT > r_now
        if cross_long == cross_short:
            continue                                     # none, or contradictory
        direction = "LONG" if cross_long else "SHORT"

        d = dctx.get(day)
        if not d:
            continue
        if d["rvol"] is None or d["rvol"] < MA_ENTRY_MIN_RVOL:
            continue
        if d["atr_pct"] is None or d["atr_pct"] < MA_ENTRY_MIN_ATR_PCT:
            continue
        if d["avg_dollar_vol"] < LOW_LIQUIDITY_DOLLAR_VOL:
            continue
        if bookings[direction] >= MAX_BOOKINGS_PER_SYMBOL_PER_DAY:
            continue

        px = bar["close"]
        tp = _live_plan(sym, direction, px, d["atr_pct"])
        if not tp:
            continue
        bookings[direction] += 1
        pos = {"symbol": sym, "direction": direction, "entry": px,
               "entry_ts": bar["ts"], "target": tp["target"], "stop": tp["stop"],
               "atr_pct": d["atr_pct"], "rvol": d["rvol"], "rsi": round(r_now, 1),
               "peak": 0.0, "peak_px": px}

    return trades


# ── driver ───────────────────────────────────────────────────────────────────

def backtest_rsi(days: int = 90, symbols: list[str] | None = None,
                 min_coverage: float = 0.0, max_symbols: int = 60) -> list[dict]:
    """Replay the live RSI engine over `days` of history.

    min_coverage: required share of a full 390-bar regular session actually
    present in the IEX feed (0.0 = take everything, as deployed).
    """
    from alphadesk.config import now_et
    from alphadesk.desk import watcher as entry_watcher
    from alphadesk.ingest import prices

    if symbols is None:
        entry_watcher.refresh_pool()
        symbols = entry_watcher.watched_symbols()
    symbols = symbols[:max_symbols]

    start = now_et() - timedelta(days=days)
    spy = prices.intraday_bars("SPY", start)
    spy_px = {b["ts"]: b["close"] for b in spy}
    spy_keys = sorted(spy_px)

    def _spy_at(ts):
        import bisect
        i = bisect.bisect_left(spy_keys, ts)
        if i >= len(spy_keys):
            i = len(spy_keys) - 1
        return spy_px[spy_keys[i]] if spy_keys else None

    all_trades: list[dict] = []
    stats = {"tested": 0, "skipped_coverage": 0, "no_data": 0}
    for n, sym in enumerate(symbols, 1):
        minute = prices.intraday_bars(sym, start)
        if len(minute) < MIN_BARS + 2:
            stats["no_data"] += 1
            continue
        sessions = len({b["ts"].astimezone(ET).date() for b in minute})
        coverage = len(minute) / (sessions * 390) if sessions else 0.0
        if coverage < min_coverage:
            stats["skipped_coverage"] += 1
            continue
        daily = _daily_bars(sym, start - timedelta(days=60))
        dctx = _daily_context(daily)
        if not dctx:
            stats["no_data"] += 1
            continue
        stats["tested"] += 1
        for t in replay_symbol(sym, minute, dctx):
            t["coverage"] = round(coverage, 3)
            up = t["direction"] == "LONG"
            t["ret_pct"] = round(((t["exit_price"] - t["entry"]) / t["entry"] * 100) if up
                                 else ((t["entry"] - t["exit_price"]) / t["entry"] * 100), 3)
            s0, s1 = _spy_at(t["entry_ts"]), _spy_at(t["exit_ts"])
            if s0 and s1:
                spy_ret = (s1 - s0) / s0 * 100
                # a short's benchmark is the inverse of the market's move
                t["alpha_pct"] = round(t["ret_pct"] - (spy_ret if up else -spy_ret), 3)
            else:
                t["alpha_pct"] = None
            t["hold_min"] = round((t["exit_ts"] - t["entry_ts"]).total_seconds() / 60)
            all_trades.append(t)
        if n % 10 == 0:
            log.info("replayed %d/%d symbols, %d trades", n, len(symbols), len(all_trades))

    log.info("coverage>=%.2f: tested %d, skipped %d, no-data %d",
             min_coverage, stats["tested"], stats["skipped_coverage"], stats["no_data"])
    all_trades.sort(key=lambda t: t["entry_ts"])
    for t in all_trades:
        t["_stats"] = stats
    return all_trades


def report(trades: list[dict], label: str = "") -> None:
    if not trades:
        print(f"\n  {label}: no trades — nothing qualified.")
        return
    stats = trades[0].get("_stats", {})
    n = len(trades)
    rets = [t["ret_pct"] for t in trades]
    alphas = [t["alpha_pct"] for t in trades if t["alpha_pct"] is not None]
    wins = sum(1 for r in rets if r > 0)
    print(f"\n══ {label} ══")
    print(f"  symbols tested {stats.get('tested','?')}  "
          f"(skipped for coverage {stats.get('skipped_coverage','?')}, "
          f"no data {stats.get('no_data','?')})")
    print(f"  trades {n}   win% {wins/n*100:5.1f}   "
          f"mean ret {sum(rets)/n:+.3f}%   median ret {sorted(rets)[n//2]:+.3f}%")
    if alphas:
        print(f"  mean alpha vs SPY {sum(alphas)/len(alphas):+.3f}%   "
              f"total alpha {sum(alphas):+.1f}%   "
              f"mean hold {sum(t['hold_min'] for t in trades)/n:.0f} min")

    print(f"\n  {'direction':10} {'n':>5} {'win%':>7} {'mean ret':>10} {'mean alpha':>11}")
    for d in ("LONG", "SHORT"):
        sub = [t for t in trades if t["direction"] == d]
        if not sub:
            continue
        sa = [t["alpha_pct"] for t in sub if t["alpha_pct"] is not None]
        print(f"  {d:10} {len(sub):5d} "
              f"{sum(1 for t in sub if t['ret_pct']>0)/len(sub)*100:6.1f}% "
              f"{sum(t['ret_pct'] for t in sub)/len(sub):+9.3f}% "
              f"{(sum(sa)/len(sa) if sa else 0):+10.3f}%")

    print(f"\n  {'exit reason':16} {'n':>5} {'mean ret':>10}")
    by = defaultdict(list)
    for t in trades:
        by[t["exit_reason"]].append(t["ret_pct"])
    for r, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        print(f"  {r:16} {len(v):5d} {sum(v)/len(v):+9.3f}%")
