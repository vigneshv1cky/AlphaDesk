"""Entry watcher — continuous per-candidate evaluation. Zero LLM.

Every earnings-adjacent candidate is watched continuously and judged purely
on its own moving-average setup — no comparison against other candidates,
no composite score, no reaction magnitude. This replaced a reaction-gated
composite-score engine (2026-08-14): "did THIS stock's own trend just start"
instead of "is this the best of today's batch" or "did this reaction clear a
threshold."

The strategy (price/MA convergence/divergence, see _entry_signal):
  • ENTRY: price crossed its 50-day SMA recently (a fresh trend just
    started), confirmed by RSI-9 momentum and relative volume, and NOT
    already re-converging (which would signal an imminent reversal).
  • REENTRY: after an exit, if price later extends further from the MA in
    the same direction (the trend continued past where we got out), a fresh
    entry is allowed without waiting for a brand new cross — capped at
    MAX_REENTRIES_PER_SYMBOL_PER_DAY total bookings per symbol+direction.
  • EXIT: the existing tiered exits (quant/watcher.py) are unchanged, plus a
    new MA-reconvergence trigger wired in from main.py's quant watch loop.

Capital-size coordination (MAX_OPEN_POSITIONS / CONCENTRATION_MAX_PER_CLUSTER)
stays wired in exactly as before — both are currently 0 (disabled) on the
live system. MAX_ENTRIES_PER_DAY is a runaway backstop, not a capital control.
"""

import asyncio
import logging

from alphadesk.config import (
    CONCENTRATION_MAX_PER_CLUSTER,
    DAILY_LOSS_STOP_PCT,
    LOW_LIQUIDITY_DOLLAR_VOL,
    MA_CONVERGENCE_LOOKBACK_DAYS,
    MA_CROSS_FRESH_DAYS,
    MA_ENTRY_MIN_RVOL,
    MAX_ENTRIES_PER_DAY,
    MAX_OPEN_POSITIONS,
    MAX_REENTRIES_PER_SYMBOL_PER_DAY,
    PAPER_TRADING,
    PLAN_STOP_ATR,
    PLAN_TARGET_ATR,
    QUANT_SCORE_FULL_CONVICTION,
    RSI_LONG_MAX,
    RSI_LONG_MIN,
    RSI_SHORT_MAX,
    RSI_SHORT_MIN,
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
# (symbol, direction) → |ma_gap_pct| at the moment of the last exit — a later
# tick may reenter the same symbol+direction once price has moved further
# from the MA than this, without needing a fresh cross. Written by
# desk/portfolio.py's close_and_exit() (the single choke-point every exit
# path funnels through), consumed by _entry_signal() below.
_reentry_state: dict[tuple[str, str], float] = {}
# (symbol, direction) → total bookings today (initial + reentries) — enforces
# MAX_REENTRIES_PER_SYMBOL_PER_DAY independent of the global MAX_ENTRIES_PER_DAY.
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
    _reentry_state.clear()
    _bookings_today.clear()


def record_exit_distance(symbol: str, direction: str, distance_pct: float) -> None:
    """Called by desk/portfolio.py's close_and_exit() — the single choke-point
    every exit path (quant watcher, bar-touch watcher, session-close sweep)
    funnels through — to remember how far price had moved from the MA at the
    moment of exit. A later tick may reenter the same symbol+direction once
    price has moved further from the MA than this, without waiting for a
    fresh cross (see _entry_signal)."""
    _reentry_state[(symbol.upper(), direction)] = abs(distance_pct)


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


def ma_trend_status(pctx: dict) -> dict:
    """The same 'converging' definition _entry_signal uses to block new
    entries, exposed for the exit side (main.py's _quantity_watch_loop) to
    reuse verbatim — one definition, two call sites. Fails OPEN
    (converging=False) on missing data, unlike the entry side's fail-closed —
    forcing an exit off absent data would strip a position of its actual
    safety net for no reason; quant/watcher.py's other five exit tiers remain
    the backstop regardless."""
    gap = pctx.get("ma_gap_pct")
    gap_prior = pctx.get("ma_gap_pct_3d_ago")
    if gap is None or gap_prior is None:
        return {"converging": False}
    return {"converging": abs(gap) < abs(gap_prior)}


def _entry_signal(sym: str, pctx: dict) -> tuple[dict | None, str | None]:
    """Rule-based MA-convergence/divergence entry gate — each candidate judged
    purely on its own technical setup, no comparison against other candidates,
    no composite score to tune against a batch. Returns (setup, None) on a
    pass, (None, reason) on a drop. Never raises.

    Entries fail CLOSED on missing MA/RSI data — better no signal than one we
    can't confirm isn't about to reverse. (The exit-side MA-reconvergence
    check in quant/watcher.py reuses this same "converging" definition but
    fails OPEN on missing data instead — see main.py's _quantity_watch_loop.)
    """
    gap = pctx.get("ma_gap_pct")
    gap_prior = pctx.get("ma_gap_pct_3d_ago")
    rsi = pctx.get("rsi_9")
    rvol = pctx.get("rvol")
    days_since_cross = pctx.get("days_since_ma_cross")

    if gap is None or gap_prior is None or rsi is None:
        return None, "insufficient MA/RSI data"

    direction = "LONG" if gap > 0 else "SHORT" if gap < 0 else None
    if direction is None:
        return None, "no MA divergence"

    if abs(gap) < abs(gap_prior):
        return None, "MA converging — blocked, imminent reversal risk"

    if direction == "LONG":
        if not (RSI_LONG_MIN <= rsi <= RSI_LONG_MAX):
            return None, f"RSI {rsi:.0f} not confirming LONG ({RSI_LONG_MIN:g}-{RSI_LONG_MAX:g})"
    else:
        if not (RSI_SHORT_MIN <= rsi <= RSI_SHORT_MAX):
            return None, f"RSI {rsi:.0f} not confirming SHORT ({RSI_SHORT_MIN:g}-{RSI_SHORT_MAX:g})"

    if rvol is None or rvol < MA_ENTRY_MIN_RVOL:
        return None, f"rvol {rvol} below {MA_ENTRY_MIN_RVOL:g}x"

    fresh_cross = days_since_cross is not None and days_since_cross <= MA_CROSS_FRESH_DAYS
    key = (sym.upper(), direction)
    reentry_bar = _reentry_state.get(key)
    # Reentry (pyramiding onto an already-established trend) does NOT need a
    # fresh cross, but does still need every other confirmation above.
    is_reentry = not fresh_cross and reentry_bar is not None and abs(gap) > reentry_bar
    if not (fresh_cross or is_reentry):
        return None, "no fresh cross, no qualifying reentry"

    if _bookings_today.get(key, 0) >= MAX_REENTRIES_PER_SYMBOL_PER_DAY:
        return None, f"per-symbol daily entry cap reached ({MAX_REENTRIES_PER_SYMBOL_PER_DAY})"

    # Informational magnitude only — NOT used to gate or rank candidates
    # against each other (the boolean chain above already decided pass/fail
    # independently per symbol). Reused for _book()'s conviction-sizing math,
    # which has zero effect on actual paper exposure (qty=1 always).
    score = round(min(100.0, abs(gap) * 3.0 + abs(rsi - 50) * 0.5
                       + max(0.0, (rvol or 0) - 1) * 5.0), 1)
    setup = {
        "direction": direction, "score": score,
        "entry_mode": "reentry" if is_reentry else "fresh_cross",
        "signals": {"ma_gap_pct": gap, "ma_gap_pct_3d_ago": gap_prior,
                    "days_since_ma_cross": days_since_cross, "rsi_9": rsi, "rvol": rvol},
    }
    return setup, None


async def score_candidate(sym: str, arts: list[dict]) -> tuple[dict | None, str | None]:
    """Fetch price context and evaluate the technical setup for one candidate.
    Returns (setup, None) on a pass, (None, reason) on a drop."""
    loop = asyncio.get_running_loop()
    pctx = await loop.run_in_executor(None, prices.get_context, sym) or {}
    if not pctx or pctx.get("sma_50") is None:
        return None, "insufficient price history"
    setup, reason = _entry_signal(sym, pctx)
    if setup is None:
        return None, reason
    setup["_pctx"] = pctx
    return setup, None


def _reset_daily_count_if_new_day() -> None:
    global _booked_today_count, _booked_today_date
    today = now_et().date()
    if _booked_today_date != today:
        _booked_today_date = today
        _booked_today_count = 0
        _reentry_state.clear()
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

    cluster = None
    if CONCENTRATION_MAX_PER_CLUSTER > 0:
        fund = await loop.run_in_executor(None, prices.get_fundamentals, sym) or {}
        sector = fund.get("sector")
        if sector:
            cluster = f"{sector}|{direction}"
            if store.cluster_take_count(cluster) >= CONCENTRATION_MAX_PER_CLUSTER:
                gate_reasons.append({"symbol": sym,
                                     "reason": f"concentration cap: {cluster} already at {CONCENTRATION_MAX_PER_CLUSTER}"})
                return None

    edge = "PRE_EARNINGS" if arts and arts[0].get("category") == "PRE_EARNINGS" else "MOMENTUM"
    horizon = pinned_horizon(edge)
    last = pctx.get("last_price")
    trade = plan.atr_plan(sym, direction, horizon, last, pctx.get("atr_pct"))
    if not trade and last:
        atr = pctx.get("atr_pct") or 2.0
        if direction == "LONG":
            trade = {"entry": round(last, 4),
                     "target": round(last * (1 + atr / 100 * PLAN_TARGET_ATR), 4),
                     "stop": round(last * (1 - atr / 100 * PLAN_STOP_ATR), 4),
                     "note": f"MA setup: {direction} {sym}", "order": "market"}
        else:
            trade = {"entry": round(last, 4),
                     "target": round(last * (1 - atr / 100 * PLAN_TARGET_ATR), 4),
                     "stop": round(last * (1 + atr / 100 * PLAN_STOP_ATR), 4),
                     "note": f"MA setup: {direction} {sym}", "order": "market"}

    # Informational sizing scale only (see _entry_signal's "score" comment) —
    # has zero effect on actual paper exposure (qty=1 always, see
    # portfolio.route_pick); kept so the ledger/UI's existing conviction
    # display has a number to show.
    sizing = min(setup["score"] / QUANT_SCORE_FULL_CONVICTION, 1.0)
    conviction = max(0, min(round(25 + sizing * 75), 100))

    spy_ctx = await loop.run_in_executor(None, prices.get_context, "SPY")
    sig = setup["signals"]
    thesis = (f"MA setup: {sym} {direction} ({setup['entry_mode']}) — "
              f"gap {sig['ma_gap_pct']:+.2f}% (was {sig['ma_gap_pct_3d_ago']:+.2f}% "
              f"{MA_CONVERGENCE_LOOKBACK_DAYS}d ago), RSI-9 {sig['rsi_9']:.0f}, "
              f"rvol {sig['rvol']:.1f}x, cross {sig['days_since_ma_cross']}d ago")
    pick_id = store.record_pick({
        "symbol": sym, "arm": "QUANT", "edge": edge,
        "source": "QUANT", "decision_id": f"q-{sym}",
        "trigger_src": "ENTRY_WATCH", "session": cur_sess,
        "direction": direction, "horizon_days": horizon,
        "cluster": cluster,   # sector|direction — for the concentration cap
        "score": setup["score"],
        "adjusted_score": conviction,
        "confidence": conviction,
        "verdict": "QUANT",
        "approved": 1,
        "triage_reason": thesis,
        "thesis": thesis,
        "debate": {"quant_signals": sig},
        "briefs": [],
        "model_tags": {"mode": "entry_watch", "entry_mode": setup["entry_mode"]},
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
    log.info("QUANT pick #%d: %s %s (%s) score=%.0f entry=%.2f stop=%.2f tgt=%.2f",
             pick_id, sym, direction, setup["entry_mode"], setup["score"],
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
    _reentry_state.pop(key, None)
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

    if MAX_OPEN_POSITIONS > 0 and store.open_position_count() >= MAX_OPEN_POSITIONS:
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
