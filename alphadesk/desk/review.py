"""Position re-evaluation — the exit half of the desk, NEWS-ONLY by design.

The team only ever OPENS positions; two things close them early: this reviewer
(on fresh information) and the position watcher (on price levels, pure code).
The split is deliberate: PRICE-BASED EXITS BELONG TO CODE — the watcher walks
minute bars and closes at target/stop with no judgment, no tokens, no temptation
to bank a winner early. This reviewer is therefore shown NO price data at all:
not the entry, not the current price, not the move, not the momentum. Its only
question is "has the THESIS been invalidated by what was reported SINCE the
call?" — an information-driven exit, never a price-driven one (design law #2).

Fail-safe: on any error it HOLDs (never auto-exits a real position because the
system hiccuped).
"""

import logging

from alphadesk.llm import call_role, wrap_data

log = logging.getLogger("alphadesk.review")

_SYSTEM = (
    "You are the position reviewer on a predictive trading desk. The desk earlier "
    "issued a call the user may have traded. Price-based exits are handled "
    "automatically by the system (target/stop levels are watched in real time by "
    "code) — you are deliberately NOT shown prices, and price action is NOT your "
    "job. Your ONLY job: has the THESIS been invalidated or materially weakened "
    "by what was reported SINCE the call was made?\n"
    "EXIT only when fresh information genuinely undermines the call: news that "
    "contradicts the catalyst, guidance cutting against the thesis, a regulatory "
    "or legal development, the rumored event being denied, or the promised "
    "catalyst resolving or evaporating. Absence of fresh news is NOT a reason to "
    "exit — a quiet tape leaves the thesis undisturbed. HOLD whenever in doubt. "
    "Give ONE sentence grounded in the actual news, not generic caution.\n"
    'Return ONLY JSON: {"decision": "HOLD|EXIT", "reason": "<one sentence>"}'
)

_SCHEMA = {
    "decision": {"type": str, "enum": ["HOLD", "EXIT"]},
    "reason": {"type": str, "maxlen": 300},
}


def review_position(pick: dict, articles: list[dict],
                decision_id: str | None = None) -> dict:
    """Re-check one open position on FRESH NEWS ONLY → {decision, reason}.
    The pick's prices are never read and never shown. HOLD is the safe default."""
    headlines = [a.get("title", "")[:140] for a in articles[:6]] or ["(no fresh news in window)"]
    user = (
        f"Original call: {pick['direction']} {pick['symbol']}, {pick['horizon_days']}-day horizon, "
        f"opened {(pick.get('ts') or '')[:16]} UTC (conviction {pick.get('adjusted_score')}).\n"
        f"Thesis: {pick.get('thesis') or pick.get('triage_reason') or ''}\n\n"
        f"Fresh news on {pick['symbol']} since the call:\n" + wrap_data("news", "\n".join(headlines))
    )
    try:
        out = call_role("review", _SYSTEM, user, schema=_SCHEMA, decision_id=decision_id)
        out.pop("_downgraded_model", None)
        return out
    except Exception as exc:  # never auto-exit on a system failure
        log.warning("Re-eval failed for %s (%s) — defaulting HOLD", pick.get("symbol"), exc)
        return {"decision": "HOLD", "reason": f"(re-evaluation unavailable: {exc})"}
