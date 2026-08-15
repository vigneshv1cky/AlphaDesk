"""Entry watcher — continuous per-candidate evaluation. Zero LLM.

Every earnings-adjacent candidate is watched continuously and judged purely
on its own technical setup — no comparison against other candidates, no
composite score, no reaction magnitude.

Positions here are session-scoped (held for hours, not weeks), so the
signal has to move on that clock. The strategy (see _entry_signal) uses
MACD and RSI TOGETHER for both entry and exit — not one indicator per job:
  • DIRECTION (trend filter): MACD(12,26,9) — the classic periods, used
    as-is rather than rescaled for intraday bars — computed on 1-min bars
    (see ingest/prices.py's get_intraday_ma_context). MACD line above its
    signal line → LONG regime; below → SHORT regime.
  • ENTRY (timing, within that regime): RSI-9 CROSSING a threshold, not
    "wait for the extreme" (only knowable in hindsight, after it's already
    reversed). LONG needs RSI crossing UP through oversold (30); SHORT
    needs RSI crossing DOWN through overbought (70) — plus relative volume
    and a minimum ATR% (a dead/near-zero-volatility stock has no room to
    reach a meaningful target/stop). No separate freshness gate beyond that.
  • EXIT: the existing tiered exits (quant/watcher.py) — target/stop/
    trailing/spike/stale/session-close — plus a signal-reversal tier that
    also uses BOTH: MACD regime flipping against the position (trend
    invalidated), OR RSI crossing the OPPOSITE threshold (a LONG's reversion
    completing at overbought, a SHORT's at oversold). The hard stop-loss
    (MA_STOP_BACKSTOP_ATR) is a deliberately wide, rarely-triggered backstop
    — this signal-based tier is the expected primary exit.

MAX_ENTRIES_PER_DAY is a runaway backstop, not a capital control.
MAX_BOOKINGS_PER_SYMBOL_PER_DAY caps how many times one symbol+direction can
be (re)booked in a day — since there's no freshness gate, a symbol can in
principle requalify on the very next tick after an exit.
"""

import asyncio
import logging

from alphadesk.config import (
    DAILY_LOSS_STOP_PCT,
    LOW_LIQUIDITY_DOLLAR_VOL,
    MA_ENTRY_MIN_ATR_PCT,
    MA_ENTRY_MIN_RVOL,
    MA_STOP_BACKSTOP_ATR,
    MAX_BOOKINGS_PER_SYMBOL_PER_DAY,
    MAX_ENTRIES_PER_DAY,
    PAPER_TRADING,
    PLAN_TARGET_ATR,
    QUANT_SCORE_FULL_CONVICTION,
    now_et,
    pinned_horizon,
    session,
)
from alphadesk.desk import plan
from alphadesk.ingest import earnings, prices
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.watcher")

_shortable_cache: dict[str, bool] = {}
_watched: dict[str, list[dict]] = {}
_booked_today_count = 0
_booked_today_date = None
# (symbol, direction) → total bookings today — enforces
# MAX_BOOKINGS_PER_SYMBOL_PER_DAY independent of the global MAX_ENTRIES_PER_DAY.
_bookings_today: dict[tuple[str, str], int] = {}


def _shortable(symbol: str) -> bool:
    """Check Alpaca for borrow availability. Cached for the process lifetime
    (borrow status doesn't flip minute to minute the way prices do)."""
    sym = symbol.upper()
    if sym in _shortable_cache:
        return _shortable_cache[sym]
    try:
        import os
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)
        asset = client.get_asset(sym)
        ok = bool(getattr(asset, "shortable", False) and getattr(asset, "easy_to_borrow", False))
        _shortable_cache[sym] = ok
        return ok
    except Exception:
        _shortable_cache[sym] = True  # fail-open: don't block on API errors
        return True


def watched_symbols() -> list[str]:
    """Symbols currently under watch — for logging/diagnostics."""
    return list(_watched)


def clear_pool() -> None:
    """Reset all watch state. For tests."""
    global _watched, _booked_today_count, _booked_today_date
    _watched = {}
    _booked_today_count = 0
    _booked_today_date = None
    _bookings_today.clear()


def refresh_pool() -> None:
    """Refresh the set of symbols under watch from the earnings calendar. Cheap
    (mostly DB reads); the only step on a slower cadence than tick(). Does NOT
    register candidates on the live price stream — the account's data plan caps
    WebSocket subscriptions at 30 symbols total, and that budget is reserved for
    SPY + open positions (quant/watcher.py's exit monitoring depends on it).
    With an uncapped watch pool (hundreds of candidates, vs. the old batch
    scanner's top-6-per-cycle), registering every candidate blew through the
    cap immediately (repeated "symbol limit exceeded" / 405 errors) and could
    starve exit-side registrations of slots. get_spread()'s bid/ask is a soft
    liquidity signal (score_candidate handles it being None), not worth the
    live-stream slot; scoring otherwise runs entirely off REST/cached prices."""
    global _watched
    candidates = earnings.drift_candidates()

    # Anti-double-dip: don't watch a symbol that already has an open position.
    held = {p["symbol"].upper() for p in store.open_taken_picks()}
    for s in list(candidates):
        if s.upper() in held:
            candidates.pop(s, None)

    # Liquidity pre-filter: don't watch a name already known illiquid (pre-armed
    # on the earnings table by earnings.arm_liquidity() every 6h — a free lookup,
    # not a live fetch). low_liquidity is None (not yet armed) for a fresh
    # candidate — fail-open there rather than dropping on missing data.
    illiquid = [sym for sym, arts in candidates.items() if arts and arts[0].get("low_liquidity")]
    for sym in illiquid:
        candidates.pop(sym, None)

    new_syms = set(candidates) - set(_watched)

    dropped = set(_watched) - set(candidates)
    _watched = candidates
    if new_syms or dropped:
        log.info("Entry watch pool: %d watched (+%d new, -%d dropped, %d illiquid pre-filtered)",
                 len(_watched), len(new_syms), len(dropped), len(illiquid))


def _entry_signal(sym: str, pctx: dict) -> tuple[dict | None, str | None]:
    """Rule-based MACD-regime + RSI-crossing entry gate — each candidate
    judged purely on its own technical setup, no comparison against other
    candidates, no composite score to tune against a batch. MACD sets the
    allowed direction (trend filter); RSI crossing a threshold times the
    entry within it (see the module docstring). Returns (setup, None) on a
    pass, (None, reason) on a drop. Never raises.

    Fails CLOSED on missing data — better no signal than a guess."""
    macd_regime = pctx.get("macd_regime")
    rsi = pctx.get("rsi_9")
    rvol = pctx.get("rvol")

    if macd_regime is None or rsi is None:
        return None, "insufficient intraday MACD/RSI data"

    if macd_regime == "LONG":
        if not pctx.get("rsi_cross_up_oversold"):
            return None, "no RSI cross up through oversold to confirm LONG"
    else:
        if not pctx.get("rsi_cross_down_overbought"):
            return None, "no RSI cross down through overbought to confirm SHORT"

    direction = macd_regime

    if rvol is None or rvol < MA_ENTRY_MIN_RVOL:
        return None, f"rvol {rvol} below {MA_ENTRY_MIN_RVOL:g}x"

    atr_pct = pctx.get("atr_pct")
    if atr_pct is None or atr_pct < MA_ENTRY_MIN_ATR_PCT:
        return None, f"volatility {atr_pct} below {MA_ENTRY_MIN_ATR_PCT:g}% ATR floor"

    key = (sym.upper(), direction)
    if _bookings_today.get(key, 0) >= MAX_BOOKINGS_PER_SYMBOL_PER_DAY:
        return None, f"per-symbol daily entry cap reached ({MAX_BOOKINGS_PER_SYMBOL_PER_DAY})"

    # Informational magnitude only — NOT used to gate or rank candidates
    # against each other (the boolean chain above already decided pass/fail
    # independently per symbol). Reused for _book()'s conviction-sizing math,
    # which has zero effect on actual paper exposure (qty=1 always).
    macd_diff = pctx.get("macd_diff") or 0.0
    score = round(min(100.0, abs(macd_diff) * 20.0 + abs(rsi - 50) * 0.5
                       + max(0.0, (rvol or 0) - 1) * 5.0), 1)
    setup = {
        "direction": direction, "score": score,
        "signals": {"macd_diff": macd_diff, "rsi_9": rsi, "rvol": rvol, "atr_pct": atr_pct},
    }
    return setup, None


async def score_candidate(sym: str, arts: list[dict]) -> tuple[dict | None, str | None]:
    """Fetch price context (daily liquidity/ATR facts + intraday MACD/RSI)
    and evaluate the technical setup for one candidate. Returns (setup, None)
    on a pass, (None, reason) on a drop."""
    loop = asyncio.get_running_loop()
    pctx = await loop.run_in_executor(None, prices.get_context, sym) or {}
    if not pctx:
        return None, "insufficient price history"
    ma_ctx = await loop.run_in_executor(None, prices.get_intraday_ma_context, sym)
    if not ma_ctx:
        return None, "insufficient intraday bar history"
    merged = {**pctx, **ma_ctx}
    setup, reason = _entry_signal(sym, merged)
    if setup is None:
        return None, reason
    setup["_pctx"] = merged
    return setup, None


def _reset_daily_count_if_new_day() -> None:
    global _booked_today_count, _booked_today_date
    today = now_et().date()
    if _booked_today_date != today:
        _booked_today_date = today
        _booked_today_count = 0
        _bookings_today.clear()


async def _book(sym: str, arts: list[dict], setup: dict, cur_sess: str,
                gate_reasons: list[dict]) -> str | None:
    """Gate + book one qualifying candidate. Returns the symbol if booked, else
    None (appending a reason to gate_reasons on rejection)."""
    global _booked_today_count
    loop = asyncio.get_running_loop()
    direction = setup["direction"]
    pctx = setup.get("_pctx") or {}
    key = (sym.upper(), direction)

    if pctx.get("low_liquidity"):
        gate_reasons.append({"symbol": sym,
                             "reason": f"low liquidity: 20d avg $vol below ${LOW_LIQUIDITY_DOLLAR_VOL:,.0f}"})
        return None

    if direction == "SHORT":
        short_ok = await loop.run_in_executor(None, _shortable, sym)
        if not short_ok:
            gate_reasons.append({"symbol": sym, "reason": "SHORT not shortable at broker"})
            return None

    edge = "PRE_EARNINGS" if arts and arts[0].get("category") == "PRE_EARNINGS" else "MOMENTUM"
    horizon = pinned_horizon(edge)
    last = pctx.get("last_price")
    # MA_STOP_BACKSTOP_ATR, not PLAN_STOP_ATR (the offline desk/workflow.py
    # path's primary stop) — this engine's primary exit is the MACD/RSI
    # signal-reversal tier; the hard stop here only needs to catch a violent
    # gap or a data outage that leaves the signal-based exit unable to fire.
    trade = plan.atr_plan(sym, direction, horizon, last, pctx.get("atr_pct"),
                          stop_atr_mult=MA_STOP_BACKSTOP_ATR)
    if not trade and last:
        atr = pctx.get("atr_pct") or 2.0
        if direction == "LONG":
            trade = {"entry": round(last, 4),
                     "target": round(last * (1 + atr / 100 * PLAN_TARGET_ATR), 4),
                     "stop": round(last * (1 - atr / 100 * MA_STOP_BACKSTOP_ATR), 4),
                     "note": f"MA setup: {direction} {sym}", "order": "market"}
        else:
            trade = {"entry": round(last, 4),
                     "target": round(last * (1 - atr / 100 * PLAN_TARGET_ATR), 4),
                     "stop": round(last * (1 + atr / 100 * MA_STOP_BACKSTOP_ATR), 4),
                     "note": f"MA setup: {direction} {sym}", "order": "market"}

    # Informational sizing scale only (see _entry_signal's "score" comment) —
    # has zero effect on actual paper exposure (qty=1 always, see
    # portfolio.route_pick); kept so the ledger/UI's existing conviction
    # display has a number to show.
    sizing = min(setup["score"] / QUANT_SCORE_FULL_CONVICTION, 1.0)
    conviction = max(0, min(round(25 + sizing * 75), 100))

    spy_ctx = await loop.run_in_executor(None, prices.get_context, "SPY")
    sig = setup["signals"]
    thesis = (f"MACD/RSI setup: {sym} {direction} — "
              f"MACD diff {sig['macd_diff']:+.4f}, RSI-9 {sig['rsi_9']:.0f}, "
              f"rvol {sig['rvol']:.1f}x, ATR {sig['atr_pct']:.1f}%")
    pick_id = store.record_pick({
        "symbol": sym, "arm": "QUANT", "edge": edge,
        "source": "QUANT", "decision_id": f"q-{sym}",
        "trigger_src": "ENTRY_WATCH", "session": cur_sess,
        "direction": direction, "horizon_days": horizon,
        "score": setup["score"],
        "adjusted_score": conviction,
        "confidence": conviction,
        "verdict": "QUANT",
        "approved": 1,
        "triage_reason": thesis,
        "thesis": thesis,
        "debate": {"quant_signals": sig},
        "briefs": [],
        "model_tags": {"mode": "entry_watch"},
        "low_liquidity": int(bool(pctx.get("low_liquidity"))),
        "skeptic_moved_score": 0.0,
        "arbiter_overrode": 0,
        "entry_price": last if pctx.get("last_trade_ts") else None,
        "spy_price": (spy_ctx or {}).get("last_price"),
        "plan_entry": (trade or {}).get("entry"),
        "plan_target": (trade or {}).get("target"),
        "plan_stop": (trade or {}).get("stop"),
        "plan_note": (trade or {}).get("note"),
        "order_type": "market",
    })
    log.info("QUANT pick #%d: %s %s score=%.0f entry=%.2f stop=%.2f tgt=%.2f",
             pick_id, sym, direction, setup["score"],
             (trade or {}).get("entry", 0), (trade or {}).get("stop", 0), (trade or {}).get("target", 0))

    if PAPER_TRADING:
        from alphadesk.desk import portfolio
        routed = await loop.run_in_executor(
            None, portfolio.route_pick, pick_id, sym, direction,
            last or (trade or {}).get("entry", 0) or 0, conviction, cur_sess)
        if routed is False:
            store.record_exit(pick_id, "not taken: broker route failed")
            log.warning("Pick #%d %s not routed to broker — not taken", pick_id, sym)
            return None

    store.mark_taken([pick_id])
    _booked_today_count += 1
    _bookings_today[key] = _bookings_today.get(key, 0) + 1
    return sym


async def tick() -> list[str]:
    """One evaluation pass over every currently-watched candidate. Each is
    judged purely on its own MA-convergence setup (_entry_signal) — no
    ranking against the rest of the pool, no slot cap. Returns symbols booked
    this tick (each graduates out of the watch pool into a live position,
    where the existing exit watcher — quant/watcher.py — takes over)."""
    if not _watched:
        return []

    _reset_daily_count_if_new_day()
    if _booked_today_count >= MAX_ENTRIES_PER_DAY:
        return []

    if DAILY_LOSS_STOP_PCT > 0 and store.today_realized_pnl_pct() <= -DAILY_LOSS_STOP_PCT:
        from alphadesk.app.alerts import notify
        notify(f"Risk rail: daily realized loss <= -{DAILY_LOSS_STOP_PCT:g}% — halted for the day", "error")
        return []

    cur_sess = session()

    # Anti-double-dip re-check (cheap; a symbol's prior position may have
    # exited since the last pool refresh, freeing it up to trade again today).
    held = {p["symbol"].upper() for p in store.open_taken_picks()}
    pool = {s: a for s, a in _watched.items() if s.upper() not in held}
    if not pool:
        return []

    sem = asyncio.Semaphore(8)   # bound concurrent yfinance fetches

    async def _guarded(sym, arts):
        async with sem:
            setup, reason = await score_candidate(sym, arts)
            return sym, arts, setup, reason

    gate_reasons: list[dict] = []
    drop_reasons: list[dict] = []
    booked: list[str] = []

    for coro in asyncio.as_completed([_guarded(s, a) for s, a in pool.items()]):
        sym, arts, setup, reason = await coro
        if setup is None:
            drop_reasons.append({"symbol": sym, "reason": reason})
            continue
        won = await _book(sym, arts, setup, cur_sess, gate_reasons)
        if won:
            booked.append(won)
            _watched.pop(won, None)
            if _booked_today_count >= MAX_ENTRIES_PER_DAY:
                break

    # Coverage ledger + anti-survivorship skip tracking — same shape as the old
    # batch scanner, just scoped to one tick's pool instead of one run's top-N.
    # record_skips() dedupes per symbol per day, so re-evaluating the same
    # still-rejected candidate every tick doesn't spam the table.
    all_reasons = gate_reasons + drop_reasons
    store.funnel_add(ingested=len(_watched) + len(booked), candidates=len(pool),
                     picked=len(booked), skipped=len(all_reasons), skip_reasons=all_reasons)
    if all_reasons:
        store.record_skips(all_reasons)
    if booked:
        board = [{"id": None, "symbol": s, "take": True} for s in booked]
        store.add_run("FIND_TRADES", board)   # kind kept as "FIND_TRADES" so
        # app/dashboard.py's api_system() (runs_summary_today's default kind)
        # keeps working unmodified — the pick-level trigger_src distinguishes
        # entry-watch bookings from the old batch scanner in the ledger itself.

    return booked
