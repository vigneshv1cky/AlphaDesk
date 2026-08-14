"""Entry watcher — continuous per-candidate evaluation. Zero LLM.

Replaces the batch scanner (desk/stream.py, removed 2026-08-13): instead of
scoring a capped pool every 5-15 min and picking the top N, every earnings
candidate is watched continuously and evaluated independently against the
same score threshold, whenever it's next due for a fresh look. No batch, no
reaction-magnitude cap, no ranking against other candidates — post-earnings
drift is an absolute per-stock judgment ("did THIS reaction clear the bar?"),
not a relative one ("is this the best of today's batch?"). Comparison-based
selection only earns its keep when something scarce forces a choice; with
position size fixed (qty=1) and capital assumed unbounded for this phase,
nothing does.

Capital-size coordination (MAX_OPEN_POSITIONS / CONCENTRATION_MAX_PER_CLUSTER)
stays wired in exactly as before and is NOT redesigned here — both are
currently 0 (disabled) on the live system. A real cross-candidate execution
gate is a deliberately deferred "Layer 2" concern, not part of this change.
MAX_ENTRIES_PER_DAY below is a different thing: not a capital control, just a
runaway backstop (an uncapped continuous watcher can in principle book far
more per day than the old top-6-per-cycle scanner ever could).
"""

import asyncio
import logging

from alphadesk.config import (
    CONCENTRATION_MAX_PER_CLUSTER,
    DAILY_LOSS_STOP_PCT,
    EARNINGS_DRIFT_DAYS,
    LOW_LIQUIDITY_DOLLAR_VOL,
    MAX_ENTRIES_PER_DAY,
    MAX_OPEN_POSITIONS,
    PAPER_TRADING,
    PLAN_STOP_ATR,
    PLAN_TARGET_ATR,
    QUANT_PREFILTER_MIN_SCORE,
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
    candidates = earnings.drift_candidates(EARNINGS_DRIFT_DAYS)

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


async def score_candidate(sym: str, arts: list[dict], moves: dict, weights: dict) -> dict:
    """Fetch context and score one candidate. Returns compute_composite()'s
    result dict, with the fetched price context stashed under "_pctx" so a
    winning candidate's booking step doesn't have to re-fetch it."""
    loop = asyncio.get_running_loop()
    pctx = await loop.run_in_executor(None, prices.get_context, sym) or {}
    move = moves.get(sym) or {}   # may be None for unmeasurable names
    # Implied move: prefer the PRE-ARMED options context (stored when the
    # reporter was armed ahead of release — instant, exact baseline); fall back
    # to a live fetch if never armed.
    implied_move = next((a.get("implied_move_pct") for a in arts
                         if a.get("implied_move_pct")), None)
    if implied_move is None:
        try:
            opt = await loop.run_in_executor(None, prices.get_options_context, sym)
            if opt:
                implied_move = opt.get("expected_move_1d_pct") or opt.get("expected_move_to_expiry_pct")
        except Exception:
            pass
    fund = await loop.run_in_executor(None, prices.get_fundamentals, sym) or {}
    sector_chg = await loop.run_in_executor(None, prices.sector_change_pct, fund.get("sector"))
    spread_pct = None
    try:
        from alphadesk.quant import stream as qstream
        sp = qstream.get_spread(sym)
        if sp and sp[0] > 0:
            spread_pct = round((sp[1] - sp[0]) / sp[0] * 100, 2)
    except Exception:
        pass
    rctx = {
        "reaction_pct": move.get("total") if move else None,
        "drift_pct": move.get("drift") if move else None,
        "gap_pct": move.get("gap") if move else None,
        "implied_move_pct": implied_move,
        "change_today": pctx.get("change_today_pct"),
        "change_5d": pctx.get("change_5d_pct"),
        "change_20d": pctx.get("change_20d_pct"),
        "rvol": pctx.get("rvol"),
        "post_vol_ratio": pctx.get("rvol"),
        "atr_pct": pctx.get("atr_pct"),
        "sector_change_pct": sector_chg,
        "market_cap": fund.get("market_cap") or pctx.get("market_cap"),
        "avg_dollar_vol": pctx.get("avg_dollar_vol"),
        "spread_pct": spread_pct,
        "short_float_pct": fund.get("short_float_pct"),
        "days_to_cover": fund.get("days_to_cover"),
    }
    from alphadesk.quant import signals as qs
    result = qs.compute_composite(rctx, weights)
    result["_pctx"] = pctx
    return result


def _reset_daily_count_if_new_day() -> None:
    global _booked_today_count, _booked_today_date
    today = now_et().date()
    if _booked_today_date != today:
        _booked_today_date = today
        _booked_today_count = 0


async def _book(sym: str, arts: list[dict], result: dict, cur_sess: str,
                gate_reasons: list[dict]) -> str | None:
    """Gate + book one qualifying candidate. Returns the symbol if booked, else
    None (appending a reason to gate_reasons on rejection)."""
    global _booked_today_count
    loop = asyncio.get_running_loop()
    direction = result["direction"]
    pctx = result.get("_pctx") or {}

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
                     "note": f"Quant: {direction} {sym}", "order": "market"}
        else:
            trade = {"entry": round(last, 4),
                     "target": round(last * (1 - atr / 100 * PLAN_TARGET_ATR), 4),
                     "stop": round(last * (1 + atr / 100 * PLAN_STOP_ATR), 4),
                     "note": f"Quant: {direction} {sym}", "order": "market"}

    # Absolute conviction scale — no batch to compare against anymore (see
    # QUANT_SCORE_FULL_CONVICTION's docstring in config.py).
    sizing = min(abs(result["score"]) / QUANT_SCORE_FULL_CONVICTION, 1.0)
    conviction = max(0, min(round(25 + sizing * 75), 100))

    spy_ctx = await loop.run_in_executor(None, prices.get_context, "SPY")
    pick_id = store.record_pick({
        "symbol": sym, "arm": "QUANT", "edge": edge,
        "source": "QUANT", "decision_id": f"q-{sym}",
        "trigger_src": "ENTRY_WATCH", "session": cur_sess,
        "direction": direction, "horizon_days": horizon,
        "cluster": cluster,   # sector|direction — for the concentration cap
        "score": result["score"],
        "adjusted_score": conviction,
        "confidence": conviction,
        "verdict": "QUANT",
        "approved": 1,
        "triage_reason": f"Quant composite={result['composite']:.1f}",
        "thesis": f"Quant pick: {sym} {direction} composite={result['composite']:.1f}",
        "debate": {"quant_signals": result.get("signals", {})},
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
             pick_id, sym, direction, result["score"],
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
    return sym


async def tick() -> list[str]:
    """One evaluation pass over every currently-watched candidate. Each is
    judged purely on its own merit against QUANT_PREFILTER_MIN_SCORE — no
    ranking against the rest of the pool, no slot cap. Returns symbols booked
    this tick (each graduates out of the watch pool into a live position,
    where the existing exit watcher — quant/watcher.py, unchanged — takes
    over)."""
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
    loop = asyncio.get_running_loop()

    # Anti-double-dip re-check (cheap; a symbol's prior position may have
    # exited since the last pool refresh, freeing it up to trade again today).
    held = {p["symbol"].upper() for p in store.open_taken_picks()}
    pool = {s: a for s, a in _watched.items() if s.upper() not in held}
    if not pool:
        return []

    from alphadesk.quant import calibrate as qc
    weights = qc.load_weights()

    # One batched download for every watched candidate's post-report move —
    # moves_since_report's own 60s TTL cache (keyed on the exact symbol set)
    # means a mostly-stable pool mostly cache-hits between ticks.
    all_items = [
        {"symbol": a.get("tickers", [sym])[0] if a.get("tickers") else sym,
         "report_date": a.get("published_at", "")[:10] if a.get("published_at") else "",
         "session": a.get("mentions", [{}])[0].get("category", "DAY")}
        for sym, arts in pool.items() for a in arts if a.get("published_at")
    ]
    moves = (await loop.run_in_executor(None, prices.moves_since_report, all_items)
             if all_items else {})

    sem = asyncio.Semaphore(8)   # bound concurrent yfinance fetches

    async def _guarded(sym, arts):
        async with sem:
            return sym, arts, await score_candidate(sym, arts, moves, weights)

    gate_reasons: list[dict] = []
    drop_reasons: list[dict] = []
    booked: list[str] = []

    for coro in asyncio.as_completed([_guarded(s, a) for s, a in pool.items()]):
        sym, arts, result = await coro
        if QUANT_PREFILTER_MIN_SCORE > 0 and result["score"] < QUANT_PREFILTER_MIN_SCORE:
            drop_reasons.append({"symbol": sym,
                                 "reason": f"quant pre-filter: score {result['score']:.1f}"})
            continue
        won = await _book(sym, arts, result, cur_sess, gate_reasons)
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
