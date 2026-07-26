"""Macro event review — when code detects a macro shock (VIX spike, rate move),
a single brief call flags which open positions may be vulnerable. Advisory only:
the review agent (review.py) makes the actual HOLD/EXIT call on the next
Find Trades run; the watcher still enforces targets/stops between runs.

Design law: code detects the shock, agents judge the impact.
"""

import json
import logging

from alphadesk.llm import LLMError, call_role, wrap_data

log = logging.getLogger("alphadesk.macro")

_SYSTEM = (
    "You are the macro risk desk. A significant macro dislocation has occurred. "
    "Your job: scan the firm's open positions and flag any that are DIRECTLY "
    "vulnerable to this specific macro event. Be conservative — only flag a "
    "position if the macro change clearly undermines the thesis.\n\n"
    "Rules:\n"
    "  • Rate shock (large yield move): flag duration-sensitive growth/tech "
    "LONGs (rising rates = discount-rate headwind), financial LONGs (falling "
    "rates = margin pressure), bond-proxy SHORTs (falling rates help them).\n"
    "  • VIX spike: flag risk-on LONGs (vol crush), SHORTs that benefit from "
    "panic, anything whose thesis assumed calm markets.\n"
    "  • If the thesis already ANTICIPATED this macro move or the position is a "
    "post-earnings momentum play with a tight stop, do NOT flag it — the code "
    "watcher handles price exits.\n"
    "  • If unsure, do NOT flag.\n\n"
    "Return ONLY JSON: {\"flagged\": [{\"pick_id\": <int>, \"symbol\": \"...\", "
    "\"concern\": \"<one line>\", \"action\": \"REVIEW\"}]}"
)

_SCHEMA = {
    "flagged": {
        "type": list, "maxitems": 10,
        "items": {
            "pick_id": {"type": int, "min": 1},
            "symbol": {"type": str, "maxlen": 10},
            "concern": {"type": str, "maxlen": 200},
            "action": {"type": str, "enum": ["REVIEW"]},
        },
    }
}


def macro_review(shock: dict, open_positions: list[dict]) -> list[dict]:
    """Flag open positions vulnerable to a macro shock. Returns list of
    flagged picks (partial dicts — advisory only, does NOT exit positions).
    Cheap haiku call; fails silently on any error."""
    if not open_positions:
        return []

    pos_summary = [
        {
            "pick_id": p["id"],
            "symbol": p["symbol"],
            "direction": p["direction"],
            "edge": p.get("edge", "?"),
            "thesis": (p.get("thesis") or "")[:200],
            "entry_price": p.get("entry_price") or p.get("broker_fill_price"),
            "has_stop": bool(p.get("plan_stop")),
        }
        for p in open_positions
    ]

    payload = {
        "shock": shock["summary"],
        "macro_snapshot": shock.get("snapshot", {}),
        "positions": pos_summary,
    }

    try:
        out = call_role(
            "brief",
            _SYSTEM,
            "Macro shock detected. Review open positions for vulnerability.\n"
            + wrap_data("macro_event", json.dumps(payload, default=str)),
            schema=_SCHEMA,
        )
        flagged = out.get("flagged", [])
        if flagged:
            log.info("Macro review: %d positions flagged for re-evaluation: %s",
                     len(flagged),
                     ", ".join(f"#{f['pick_id']} {f['symbol']}" for f in flagged))
        return flagged
    except LLMError as exc:
        log.warning("Macro review call failed: %s", exc)
        return []


def book_hedge(parent: dict, entry_px: float, shock_summary: str,
               session: str, spy_price: float | None = None) -> int | None:
    """Book a companion SHORT hedge for a LONG position threatened by a macro shock.
    The hedge is a mechanical position — no LLM, pure code:
      - Entry at the current extended-hours price
      - Target = parent's plan_stop (if the long stops out, the hedge captures the full move)
      - Stop = entry + 3% (if the shock is a false alarm, cut the hedge quickly)
      - Closes when the parent exits (watcher handles this)
    Returns the hedge's pick_id, or None if the parent is not a LONG or no entry price."""
    from datetime import timezone as tz
    from datetime import datetime

    from alphadesk.ledger import store

    direction = parent.get("direction", "").upper()
    if direction != "LONG":
        return None  # only hedge longs through overnight shocks
    if not entry_px or entry_px <= 0:
        return None

    plan_entry = round(float(entry_px), 4)
    plan_target = parent.get("plan_stop")  # if the long dies at its stop, the hedge wins
    if not plan_target or plan_target >= plan_entry:
        # No usable parent stop → target at entry - 2% (reasonable short target)
        plan_target = round(plan_entry * 0.98, 4)
    plan_stop = round(plan_entry * 1.03, 4)  # 3% above entry = hedge invalidated

    ts = datetime.now(tz.utc).isoformat()
    remaining = int(parent.get("horizon_days", 1))

    row = {
        "ts": ts,
        "symbol": parent["symbol"],
        "arm": "HEDGE",
        "edge": "MOMENTUM",
        "trigger_src": "MACRO_HEDGE",
        "session": session,
        "direction": "SHORT",
        "horizon_days": remaining,
        "score": parent.get("adjusted_score") or 50,
        "adjusted_score": parent.get("adjusted_score") or 50,
        "confidence": parent.get("confidence") or 50,
        "verdict": "STRONG",
        "approved": 0,
        "taken": 1,
        "hedge_of": parent["id"],
        "thesis": f"Macro hedge against #{parent['id']} {parent['symbol']}: {shock_summary}. "
                  f"Hedge captures the overnight gap if the parent long's thesis is broken.",
        "plan_entry": plan_entry,
        "plan_target": plan_target,
        "plan_stop": plan_stop,
        "plan_note": f"HEDGE SHORT @ {plan_entry} — auto-close when parent #{parent['id']} exits",
        "order_type": "market",
        "entry_price": plan_entry,  # extended-hours fill at current price
        "spy_price": spy_price,
        "low_liquidity": parent.get("low_liquidity", 0),
    }
    pid = store.record_pick(row)
    log.info("Booked hedge #%d SHORT %s @ %s (protects #%d %s) — %s",
             pid, parent["symbol"], plan_entry, parent["id"],
             parent.get("direction"), shock_summary)
    return pid


_MACRO_SCOUT_SYSTEM = (
    "You are the macro shock trading desk. A significant macro dislocation just "
    "occurred in extended hours. The regular market opens soon, and the gap will "
    "be priced in at the open. Your job: find 1-3 US-listed stocks that are "
    "DIRECTLY positioned to PROFIT from this specific shock over the next 1-2 "
    "trading days — BEFORE the open prices them in.\n\n"
    "Rules:\n"
    "  • Central bank rate hike: look for financials in that country, currency ETFs, "
    "inverse equity ETFs, volatility products.\n"
    "  • VIX spike / panic: look for VIX-related products, gold miners, safe havens, "
    "inverse ETFs. SHORT risk-on names that will gap down.\n"
    "  • Trade war / tariff shock: look for domestic producers of the affected "
    "goods, competitors of the targeted country's exports.\n"
    "  • Oil supply shock: look for energy producers, refiners, pipeline companies.\n"
    "  • Yield curve move: look for bank ETFs (steeper), growth SHORTs (flatter "
    "inversion), bond ETFs.\n"
    "  • Be SPECIFIC: name the stock, the direction, and the exact causal chain.\n"
    "  • Prefer LIQUID names (avg daily $ volume > $100M) so the trade is executable.\n"
    "  • Limit to 3 picks max. Skip if nothing is clearly positioned.\n"
    'Return ONLY JSON: {"picks": [{"symbol": "...", "direction": "LONG|SHORT", '
    '"edge_hint": "MOMENTUM", "reason": "<one-line causal chain>"}]}'
)

_MACRO_SCOUT_SCHEMA = {
    "picks": {
        "type": list, "maxitems": 3,
        "items": {
            "symbol": {"type": str, "symbol": True},
            "direction": {"type": str, "enum": ["LONG", "SHORT"]},
            "edge_hint": {"type": str, "enum": ["MOMENTUM"]},
            "reason": {"type": str, "maxlen": 250},
        },
    }
}


def macro_scout(shock: dict, movers: list[dict]) -> list[dict]:
    """Find stocks positioned to profit from a macro shock. Returns list of
    {symbol, direction, edge_hint, reason}. Single call, no full debate."""
    from alphadesk.ingest.prices import macro_snapshot

    macro = macro_snapshot()
    parts = [f"MACRO SHOCK: {shock['summary']}"]
    if macro:
        parts.append(f"Current backdrop: 10Y={macro.get('us10y_pct')}%, "
                     f"Fed proxy={macro.get('fed_funds_proxy_pct')}%, VIX={macro.get('vix')}")
    if movers:
        m_str = ", ".join(
            f"{m['symbol']} {m['direction']} {m['change_pct']}%"
            for m in movers[:10]
        )
        parts.append(f"Top movers right now: {m_str}")

    user = "\n".join(parts)
    try:
        out = call_role(
            "scout", _MACRO_SCOUT_SYSTEM, user, schema=_MACRO_SCOUT_SCHEMA,
        )
        picks = out.get("picks", [])
        if picks:
            log.info("Macro scout: %d picks for shock '%s': %s",
                     len(picks), shock["summary"][:60],
                     ", ".join(f"{p['symbol']} {p['direction']}" for p in picks))
        return picks
    except LLMError as exc:
        log.warning("Macro scout call failed: %s", exc)
        return []


_MACRO_TRADE_SYSTEM = (
    "You are the macro execution desk. A macro shock has been identified and the "
    "scout has nominated a stock as a beneficiary. Your job: validate the thesis "
    "and produce a quick trade plan. You have ONE shot — be decisive.\n\n"
    "Steps:\n"
    "  1. Verify the causal chain makes sense (if not, PASS).\n"
    "  2. Set direction (SHORT or LONG) and confidence (0-100).\n"
    "  3. Set a tight stop (3-5% away — macro trades are binary, cut fast if wrong).\n"
    "  4. Set a realistic 1-2 day target based on the expected gap.\n"
    "  5. Return a verdict: APPROVED with adjusted_score, or PASS.\n\n"
    "Return ONLY JSON: {\"verdict\": \"APPROVED|PASS\", \"direction\": \"LONG|SHORT\", "
    "\"adjusted_score\": <0-100>, \"confidence\": <0-100>, \"thesis\": \"<one line>\", "
    "\"plan_entry\": <current price>, \"plan_target\": <price>, \"plan_stop\": <price>, "
    "\"plan_note\": \"<one line>\"}"
)

_MACRO_TRADE_SCHEMA = {
    "verdict": {"type": str, "enum": ["APPROVED", "PASS"]},
    "direction": {"type": str, "enum": ["LONG", "SHORT"], "optional": True},
    "adjusted_score": {"type": (int, float), "min": 0, "max": 100, "optional": True},
    "confidence": {"type": (int, float), "min": 0, "max": 100, "optional": True},
    "thesis": {"type": str, "maxlen": 300, "optional": True},
    "plan_entry": {"type": (int, float), "min": 0, "optional": True},
    "plan_target": {"type": (int, float), "min": 0, "optional": True},
    "plan_stop": {"type": (int, float), "min": 0, "optional": True},
    "plan_note": {"type": str, "maxlen": 120, "optional": True},
}


def macro_trade(symbol: str, scout_reason: str, direction: str,
                entry_px: float, shock: dict,
                spy_price: float | None = None) -> dict | None:
    """One-shot trade validation + plan for a macro scout pick. Returns a ledger-ready
    row dict or None if the verdict is PASS. Single call, no debate — speed matters."""
    user = (
        f"Symbol: {symbol}\n"
        f"Scout direction: {direction}\n"
        f"Scout chain: {scout_reason}\n"
        f"Current price (ext-hours): {entry_px}\n"
        f"Macro shock: {shock['summary']}\n"
        f"Macro snapshot: {json.dumps(shock.get('snapshot', {}))}\n"
    )
    try:
        out = call_role(
            "scout", _MACRO_TRADE_SYSTEM, user, schema=_MACRO_TRADE_SCHEMA,
        )
    except LLMError as exc:
        log.warning("Macro trade call for %s failed: %s", symbol, exc)
        return None

    if out.get("verdict") != "APPROVED":
        return None

    direction = out.get("direction", direction)
    entry = out.get("plan_entry") or entry_px
    target = out.get("plan_target")
    stop = out.get("plan_stop")

    # Coherence rails (same as plan.py)
    if not (entry and target and stop and entry > 0 and target > 0 and stop > 0):
        return None
    if direction == "LONG" and not (stop < entry < target):
        return None
    if direction == "SHORT" and not (target < entry < stop):
        return None

    from datetime import timezone as tz
    from datetime import datetime
    ts = datetime.now(tz.utc).isoformat()

    return {
        "ts": ts,
        "symbol": symbol,
        "arm": "TEAM",
        "edge": "MOMENTUM",
        "trigger_src": "MACRO_SHOCK",
        "session": "PRE",  # always extended-hours entry for macro trades
        "direction": direction,
        "horizon_days": 1,
        "score": out.get("adjusted_score", 50),
        "adjusted_score": out.get("adjusted_score", 50),
        "confidence": out.get("confidence", 50),
        "verdict": "STRONG",
        "approved": 1,
        "taken": 1,
        "thesis": out.get("thesis", scout_reason),
        "triage_reason": f"MACRO SHOCK: {shock['summary'][:150]}",
        "plan_entry": round(float(entry), 4),
        "plan_target": round(float(target), 4),
        "plan_stop": round(float(stop), 4),
        "plan_note": out.get("plan_note", f"Macro trade: {shock['summary'][:80]}"),
        "order_type": "market",
        "entry_price": round(float(entry), 4),
        "spy_price": spy_price,
    }
