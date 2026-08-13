"""research_run() — pure quant batch pipeline. Zero LLM."""

import asyncio
import logging
import time
import uuid

from alphadesk.config import SYMBOL_REPICK_COOLDOWN_MIN
from alphadesk.desk import plan
from alphadesk.ingest import prices
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.workflow")

_repick_at: dict[str, float] = {}
_cooldowns_seeded = False


def _seed_cooldowns_from_ledger() -> None:
    global _cooldowns_seeded
    _cooldowns_seeded = True
    try:
        import sqlite3
        from datetime import datetime, timezone
        from alphadesk.config import DATA_DIR
        with sqlite3.connect(DATA_DIR / "ledger.db") as conn:
            rows = conn.execute(
                "SELECT symbol, max(ts) FROM picks WHERE arm='QUANT'"
                f" AND ts >= datetime('now', '-{SYMBOL_REPICK_COOLDOWN_MIN} minutes')"
                " GROUP BY symbol"
            ).fetchall()
        now_mono, now_utc = time.monotonic(), datetime.now(timezone.utc)
        for sym, ts in rows:
            age_s = (now_utc - datetime.fromisoformat(ts)).total_seconds()
            remaining = SYMBOL_REPICK_COOLDOWN_MIN * 60 - age_s
            if remaining > 0:
                _repick_at[sym] = now_mono + remaining
        if rows:
            log.info("Seeded %d cooldowns from ledger", len(rows))
    except Exception as exc:
        log.warning("Cooldown seeding failed: %s", exc)


async def research_run(candidates: dict[str, list[dict]], trigger_src: str = "STREAM") -> list[int]:
    loop = asyncio.get_running_loop()
    if not _cooldowns_seeded:
        _seed_cooldowns_from_ledger()
    now = time.monotonic()

    # Entries are OPEN-only (matches desk/stream.py's live pipeline gate). Without
    # this, a CLOSED-time run stamped its pick "PRE" with entry_price left None
    # for "the next 4:00 fill" — but entry_fill_time() only actually queues a
    # fill for a pick stamped literally "CLOSED", not "PRE", so that pick's fill
    # moment was already in the past by the time OPEN started and it was
    # immediately marked "not taken: never filled in its session" — a dead pick
    # from the moment it was booked, any time `python -m alphadesk.main desk`
    # was run outside OPEN hours.
    from alphadesk.config import entry_allowed, session as sess_fn
    if sess_fn() != "OPEN" or not entry_allowed():
        return []

    eligible = {s: a for s, a in candidates.items() if _repick_at.get(s, 0.0) <= now}
    if not eligible:
        return []

    from alphadesk.quant import signals as qs
    from alphadesk.quant import calibrate as qc
    weights = qc.load_weights()

    ids = []
    for sym, arts in eligible.items():
        _repick_at[sym] = time.monotonic() + SYMBOL_REPICK_COOLDOWN_MIN * 60
        pctx = await loop.run_in_executor(None, prices.get_context, sym) or {}
        fund = await loop.run_in_executor(None, prices.get_fundamentals, sym) or {}
        rctx = {
            "change_today": pctx.get("change_today_pct"),
            "change_5d": pctx.get("change_5d_pct"),
            "rvol": pctx.get("rvol"),
            "atr_pct": pctx.get("atr_pct"),
            "market_cap": fund.get("market_cap"),
            "avg_dollar_vol": pctx.get("avg_dollar_vol"),
            "short_float_pct": fund.get("short_float_pct"),
            "days_to_cover": fund.get("days_to_cover"),
        }
        result = qs.compute_composite(rctx, weights)
        if result["score"] < 5:
            continue

        direction = result["direction"]
        last = pctx.get("last_price")
        if not last:
            continue

        from alphadesk.config import pinned_horizon
        horizon = pinned_horizon("MOMENTUM")
        trade = plan.atr_plan(sym, direction, horizon, last, pctx.get("atr_pct"))

        spy_ctx = await loop.run_in_executor(None, prices.get_context, "SPY")
        # sess_fn() is guaranteed "OPEN" here — checked at the top of this run.
        pick_id = store.record_pick({
            "symbol": sym, "arm": "QUANT", "edge": "MOMENTUM",
            "source": "BATCH", "decision_id": f"b-{sym}-{uuid.uuid4().hex[:8]}",
            "trigger_src": trigger_src, "session": "OPEN",
            "direction": direction, "horizon_days": horizon,
            "score": result["score"], "adjusted_score": result["score"],
            "confidence": 50, "verdict": "QUANT", "approved": 1,
            "triage_reason": f"Batch quant composite={result['composite']:.1f}",
            "thesis": f"Quant: {sym} {direction} composite={result['composite']:.1f}",
            "debate": {"quant_signals": result.get("signals", {})},
            "briefs": [], "model_tags": {"mode": "quant"},
            "low_liquidity": int(bool(pctx.get("low_liquidity"))),
            "skeptic_moved_score": 0.0, "arbiter_overrode": 0,
            "entry_price": last,
            "spy_price": (spy_ctx or {}).get("last_price"),
            "plan_entry": (trade or {}).get("entry"),
            "plan_target": (trade or {}).get("target"),
            "plan_stop": (trade or {}).get("stop"),
            "plan_note": (trade or {}).get("note"),
            "order_type": "market",
        })
        ids.append(pick_id)
        store.mark_taken([pick_id])
        log.info("QUANT #%d %s %s %dd score=%.0f",
                 pick_id, sym, direction, horizon, result["score"])

    return ids
