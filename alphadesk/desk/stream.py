"""Find Trades — pure quant pipeline. Zero LLM.

Flow: earnings calendar → quant signals rank → top N candidates →
ATR plan → ledger. The calibrator learns from graded outcomes.
"""

import asyncio
import logging
from datetime import datetime, timezone

from alphadesk.config import (
    EARNINGS_DRIFT_DAYS,
    QUANT_PREFILTER_MIN_SCORE,
    REPICK_COOLDOWN_HOURS,
    entry_fill_time,
    now_et,
    pinned_horizon,
    session,
)
from alphadesk.desk import plan
from alphadesk.ingest import earnings, prices
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.stream")

_shortable_cache: dict[str, bool] = {}


def _shortable(symbol: str) -> bool:
    """Check Alpaca for borrow availability. Cached per run."""
    sym = symbol.upper()
    if sym in _shortable_cache:
        return _shortable_cache[sym]
    try:
        from alpaca.trading.client import TradingClient
        import os
        client = TradingClient(
            os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)
        asset = client.get_asset(sym)
        ok = bool(getattr(asset, "shortable", False) and getattr(asset, "easy_to_borrow", False))
        _shortable_cache[sym] = ok
        return ok
    except Exception:
        _shortable_cache[sym] = True  # fail-open: don't block on API errors
        return True

log = logging.getLogger("alphadesk.stream")


def _ev(_type: str, **data):
    return {"type": _type, **data}


_pending_run_picks: list[int] = []
_run_lock = asyncio.Lock()


async def stream_find_trades(hours: float = 48.0, max_picks: int = 6,
                             is_disconnected=None):
    if _run_lock.locked():
        yield _ev("status", msg="A Find Trades run is already in progress.")
        yield _ev("done", board=[])
        return
    async with _run_lock:
        async for ev in _stream_find_trades_inner(hours, max_picks, is_disconnected):
            yield ev


async def _stream_find_trades_inner(hours: float = 48.0, max_picks: int = 6,
                                    is_disconnected=None):
    loop = asyncio.get_running_loop()
    global _pending_run_picks

    async def _gone() -> bool:
        return bool(is_disconnected and await is_disconnected())

    if _pending_run_picks:
        rolled = await loop.run_in_executor(None, store.delete_picks, list(_pending_run_picks))
        if rolled:
            log.info("Previous run rolled back %d in-progress pick(s)", rolled)
    _pending_run_picks = []

    yield _ev("status", msg="Reading the earnings calendar…")
    since = datetime.now(timezone.utc).timestamp() - hours * 3600
    since_dt = datetime.fromtimestamp(since, tz=timezone.utc)

    candidates = await loop.run_in_executor(None, earnings.drift_candidates, EARNINGS_DRIFT_DAYS)
    earnings_syms = {s.upper() for s in candidates}
    log.info("Earnings drift: %d candidates with material reaction", len(candidates))
    yield _ev("status", msg=f"Earnings drift: {len(candidates)} candidates with material reaction")

    # Anti-double-dip
    open_positions = await loop.run_in_executor(None, store.open_taken_picks)
    held = {p["symbol"].upper() for p in open_positions}
    cooling = await loop.run_in_executor(None, store.symbols_debated_since, REPICK_COOLDOWN_HOURS)
    for s in list(candidates):
        su = s.upper()
        if su in held or su in cooling:
            candidates.pop(s, None)
    log.info("After anti-double-dip: %d candidates remain (%d held, %d cooling)",
             len(candidates), len(held), len(cooling))

    if not candidates:
        await loop.run_in_executor(None, store.add_run, "FIND_TRADES", [])
        yield _ev("status", msg="No new earnings candidates.")
        yield _ev("done", board=[])
        return

    # Quant scoring — limited to prevent yfinance rate-limiting on large slates
    from alphadesk.quant import signals as qs
    from alphadesk.quant import calibrate as qc
    weights = qc.load_weights()

    # Cap candidates to score: order by reaction magnitude, take top 100
    scored_candidates = sorted(candidates.items(), key=lambda kv: -abs(
        next((a.get("reaction_pct", 0) for a in kv[1] if a.get("reaction_pct")), 0)),
    )[:100]

    scored: list[tuple[str, float, str, dict]] = []
    for sym, arts in scored_candidates:
        if await _gone():
            return
        pctx = await loop.run_in_executor(None, prices.get_context, sym) or {}
        moves = await loop.run_in_executor(
            None, lambda a=arts: prices.moves_since_report(
                [{"symbol": a.get("tickers", [sym])[0] if a.get("tickers") else sym,
                  "report_date": a.get("published_at", "")[:10] if a.get("published_at") else "",
                  "session": a.get("mentions", [{}])[0].get("category", "DAY")}
                 for a in a if a.get("published_at")])) if any(a.get("published_at") for a in arts) else {}
        move = moves.get(sym, {})
        implied_move = None
        try:
            opt = await loop.run_in_executor(None, prices.get_options_context, sym)
            if opt:
                implied_move = opt.get("expected_move_pct")
        except Exception:
            pass
        fund = await loop.run_in_executor(None, prices.get_fundamentals, sym) or {}
        sector_chg = await loop.run_in_executor(
            None, prices.sector_change_pct, fund.get("sector"))
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
        result = qs.compute_composite(rctx, weights)
        if QUANT_PREFILTER_MIN_SCORE > 0 and result["score"] < QUANT_PREFILTER_MIN_SCORE:
            continue
        scored.append((sym, result["composite"], result["direction"], result))

    # Sort by score, take top N
    scored.sort(key=lambda x: -abs(x[1]))
    top = scored[:max_picks]
    if top:
        log.info("Quant scored %d candidates → top %d: %s",
                 len(scored), len(top),
                 ", ".join(f"{s[0]}={s[1]:+.0f}" for s in top[:8]))

    if not top:
        await loop.run_in_executor(None, store.add_run, "FIND_TRADES", [])
        yield _ev("status", msg="No candidates passed the quant filter.")
        yield _ev("done", board=[])
        return

    # Liquidity gate + shortability check
    cur_sess = session()
    # Night (CLOSED, 20:00–4:00) is not tradeable: the market is closed, so a pick
    # decided then enters at the next 4:00 PRE open. Stamp it PRE so it lives on the
    # Pre-Market page (and its session-close exit is the PRE close, not a phantom
    # night window).
    stamp_sess = "PRE" if cur_sess == "CLOSED" else cur_sess
    board = []
    picks_count = 0
    max_score = max(abs(s[1]) for s in top) if top else 1

    for sym, composite, direction, qscore in top:
        if await _gone():
            return
        pctx = await loop.run_in_executor(None, prices.get_context, sym) or {}
        if cur_sess in ("PRE", "AFTER") and not pctx.get("last_trade_ts"):
            yield _ev("gate", symbol=sym, reason="no trades in extended session")
            continue

        # Shortability check: verify Alpaca has borrow before booking SHORT
        if direction == "SHORT":
            short_ok = await loop.run_in_executor(None, _shortable, sym)
            if not short_ok:
                yield _ev("gate", symbol=sym,
                          reason="SHORT skipped — not shortable at broker")
                continue

        picks_count += 1
        yield _ev("triage_pick", symbol=sym, edge="MOMENTUM",
                  reason=f"Quant: {qscore['score']:.0f} {direction}")

        horizon = pinned_horizon("MOMENTUM")
        last = pctx.get("last_price")
        trade = plan.atr_plan(sym, direction, horizon, last, pctx.get("atr_pct"))
        if not trade and last:
            from alphadesk.config import PLAN_TARGET_ATR, PLAN_STOP_ATR
            atr = pctx.get("atr_pct") or 2.0
            if direction == "LONG":
                trade = {"entry": round(last, 4),
                         "target": round(last * (1 + atr/100 * PLAN_TARGET_ATR), 4),
                         "stop": round(last * (1 - atr/100 * PLAN_STOP_ATR), 4),
                         "note": f"Quant: {direction} {sym}", "order": "market"}
            else:
                trade = {"entry": round(last, 4),
                         "target": round(last * (1 - atr/100 * PLAN_TARGET_ATR), 4),
                         "stop": round(last * (1 + atr/100 * PLAN_STOP_ATR), 4),
                         "note": f"Quant: {direction} {sym}", "order": "market"}

        # Kelly-style sizing: scale conviction by signal strength vs max in run
        sizing = min(abs(qscore["score"]) / max(max_score, 1), 1.0)
        conviction = round(25 + sizing * 75)  # 25-100 range

        spy_ctx = await loop.run_in_executor(None, prices.get_context, "SPY")
        pick_id = store.record_pick({
            "symbol": sym, "arm": "QUANT", "edge": "MOMENTUM",
            "source": "QUANT", "decision_id": f"q-{sym}",
            "trigger_src": "FIND_TRADES", "session": stamp_sess,
            "direction": direction, "horizon_days": horizon,
            "score": qscore["score"],
            "adjusted_score": conviction,
            "confidence": conviction,
            "verdict": "QUANT",
            "approved": 1,
            "triage_reason": f"Quant composite={composite:.1f}",
            "thesis": f"Quant pick: {sym} {direction} composite={composite:.1f}",
            "debate": {"quant_signals": qscore.get("signals", {})},
            "briefs": [],
            "model_tags": {"mode": "quant_only"},
            "low_liquidity": int(bool(pctx.get("low_liquidity"))),
            "skeptic_moved_score": 0.0,
            "arbiter_overrode": 0,
            "entry_price": last if cur_sess != "CLOSED" and pctx.get("last_trade_ts") else None,
            "spy_price": (spy_ctx or {}).get("last_price"),
            "plan_entry": (trade or {}).get("entry"),
            "plan_target": (trade or {}).get("target"),
            "plan_stop": (trade or {}).get("stop"),
            "plan_note": (trade or {}).get("note"),
            "order_type": "market",
        })
        log.info("QUANT pick #%d: %s %s score=%.0f entry=%.2f stop=%.2f tgt=%.2f",
                 pick_id, sym, direction, qscore["score"],
                 (trade or {}).get("entry", 0),
                 (trade or {}).get("stop", 0),
                 (trade or {}).get("target", 0))
        row = {
            "id": pick_id, "symbol": sym, "direction": direction,
            "horizon_days": horizon, "edge": "MOMENTUM",
            "conviction": conviction, "confidence": conviction,
            "verdict": "QUANT", "approved": True,
            "flipped": False,
            "summary": f"Quant: {direction} score={qscore['score']:.0f} size={conviction:.0f}",
            "plan": trade, "take": True,
        }
        board.append(row)
        _pending_run_picks.append(pick_id)
        yield _ev("decision", **row)

    for row in board:
        row["take"] = True
    store.add_run("FIND_TRADES", board)
    store.mark_taken([r["id"] for r in board])
    _pending_run_picks = []
    yield _ev("status", msg=f"Run complete — {picks_count} quant pick(s)")
    yield _ev("done", board=board)
