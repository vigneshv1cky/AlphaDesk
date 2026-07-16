"""Miss post-mortem — 'why didn't we trade this?'

You bring a name the desk should have caught; this traces our OWN logs to find
where it fell out of the funnel, then an opus analyst diagnoses whether the miss
was a DATA problem, a JUDGMENT problem, or nothing (correctly skipped, got lucky).

Log-tracing only (v1): it reads what the ledger recorded. If there's no trace at
all, it can't diagnose the ingestion/extraction gap — it says so and flags that a
full reconstruction run would be needed. This is a human-in-the-loop diagnostic:
it surfaces the evidence trail and a testable hypothesis; YOU decide what to act on.

Hindsight guard: you only ever bring the misses that turned out great, while the
desk correctly skipped many similar names that went nowhere. Every diagnosis must
weigh that base rate and refuse to manufacture a fix that would also let losers in.
"""

import json
import logging

from alphadesk.config import in_universe
from alphadesk.ledger import store
from alphadesk.llm import call_role, wrap_data

log = logging.getLogger("alphadesk.postmortem")

_SYSTEM = (
    "You are the post-mortem analyst for a predictive stock research desk. The "
    "user names a stock they believe the desk should have traded and missed. You "
    "are given the desk's OWN record of what it did with that symbol (rejections "
    "with full debate, and/or triage skips with reasons), plus the funnel stage "
    "where it fell out.\n\n"
    "Your job: explain WHY it was missed and whether the miss is worth fixing. "
    "Classify the fix as:\n"
    "- DATA: the news never reached us / the ticker wasn't extracted (a coverage "
    "or enrichment gap, not a reasoning error).\n"
    "- PROMPT: a stage saw it and reasoned poorly (e.g. triage called it "
    "'already priced' too early, or the skeptic's killer concern was wrong).\n"
    "- BUG: a mechanical fault (stale liquidity field, wrong universe check).\n"
    "- NONE: the skip/rejection reasoning was actually SOUND — this name just got "
    "lucky. Say so plainly; do NOT invent a fix.\n\n"
    "CRITICAL — hindsight guard: the user only brings you winners. The desk "
    "correctly skipped many similar names that went nowhere for the SAME reason. "
    "Before proposing a fix, ask whether that fix would also have admitted known "
    "losers. State that base-rate risk honestly in hindsight_risk. A good desk "
    "misses some winners on purpose; a fix that catches this one by lowering "
    "selectivity may cost more than it earns.\n\n"
    'Return ONLY JSON: {"what_happened": "<plain summary of where it fell out>", '
    '"diagnosis": "<why we missed it>", "fix_type": "DATA|PROMPT|BUG|NONE", '
    '"suggested_fix": "<concrete, testable — or \'none, correctly skipped\'>", '
    '"hindsight_risk": "<the base-rate caveat for this specific fix>"}'
)

_SCHEMA = {
    "what_happened": {"type": str, "maxlen": 600},
    "diagnosis": {"type": str, "maxlen": 900},
    "fix_type": {"type": str, "enum": ["DATA", "PROMPT", "BUG", "NONE"]},
    "suggested_fix": {"type": str, "maxlen": 700},
    "hindsight_risk": {"type": str, "maxlen": 600},
}


def _classify(symbol: str, traces: list[dict], skips: list[dict]) -> str:
    """Code owns the stage — it's a fact of what the ledger contains, not a judgment."""
    if not in_universe(symbol):
        return "NOT_TRADABLE"
    if any(int(t.get("approved") or 0) for t in traces):
        return "TAKEN"           # we DID trade it — 'miss' is really sizing/horizon/exit
    if traces:
        return "COMMITTEE_REJECT"  # debated in full, rejected — transcript available
    if skips:
        return "TRIAGE_SKIP"       # surfaced as a candidate, triage passed — reason logged
    return "NO_TRACE"              # never evaluated — coverage/extraction gap (undiagnosable from logs)


def _trace_digest(traces: list[dict]) -> list[dict]:
    """Compact each rejection for the prompt (drop heavy JSON, keep the reasoning)."""
    out = []
    for t in traces:
        debate = {}
        try:
            debate = json.loads(t.get("debate") or "{}")
        except Exception:
            debate = {}
        out.append({
            "when": (t.get("ts") or "")[:16],
            "arm": t.get("arm"),
            "direction": t.get("direction"),
            "horizon_days": t.get("horizon_days"),
            "score": t.get("score"),
            "verdict": t.get("verdict"),
            "approved": bool(t.get("approved")),
            "triage_reason": t.get("triage_reason"),
            "thesis": t.get("thesis"),
            "skeptic_concerns": [c.get("claim") for c in (debate.get("concerns") or [])],
            "arbiter_summary": debate.get("arbiter_summary"),
            "outcome_alpha_net": t.get("alpha_net"),
        })
    return out


def diagnose_miss(symbol: str, note: str = "", days: int = 21) -> dict:
    """Trace where `symbol` fell out of the funnel over the last `days`, then
    diagnose the miss. Returns the code-determined stage, the evidence trail, and
    the analyst's verdict. Never raises — LLM failure degrades to a stage-only report."""
    symbol = symbol.upper().strip()
    traces = store.symbol_traces(symbol, days)
    skips = store.symbol_skips(symbol, days)
    stage = _classify(symbol, traces, skips)

    evidence = {
        "in_universe": in_universe(symbol),
        "rejections": _trace_digest([t for t in traces if not int(t.get("approved") or 0)]),
        "approved": _trace_digest([t for t in traces if int(t.get("approved") or 0)]),
        "triage_skips": skips[:10],
    }

    # Structural stage: no LLM needed — it's a fact, not a judgment.
    if stage == "NOT_TRADABLE":
        return {**_STAGE_LABELS[stage], "symbol": symbol, "stage": stage, "evidence": evidence,
                "what_happened": f"{symbol} is not in the tradable universe (not an active "
                                 "Alpaca US equity), so it can never be an output of a decision.",
                "diagnosis": "Structural exclusion — the desk is scoped to tradable US equities.",
                "fix_type": "NONE", "suggested_fix": "none — outside mandate by design.",
                "hindsight_risk": "n/a"}

    user = (
        f"Symbol the user thinks we missed: {symbol}\n"
        f"Lookback window: last {days} days\n"
        f"Funnel stage where it fell out (determined from our logs): {stage}\n\n"
        f"User's note on the opportunity:\n{wrap_data('user_note', note or '(none given)')}\n\n"
        f"What our desk actually recorded for {symbol}:\n"
        + wrap_data("desk_record", json.dumps(evidence, default=str))
    )
    try:
        verdict = call_role("miss", _SYSTEM, user, schema=_SCHEMA, decision_id=f"miss-{symbol}")
    except Exception as exc:  # degrade to a stage-only report, never crash the endpoint
        log.warning("Miss post-mortem LLM failed for %s: %s", symbol, exc)
        verdict = {
            "what_happened": _STAGE_LABELS[stage]["stage_label"],
            "diagnosis": f"(diagnosis unavailable — {exc})",
            "fix_type": "NONE", "suggested_fix": "retry", "hindsight_risk": "n/a",
        }
    verdict.pop("_downgraded_model", None)
    return {**_STAGE_LABELS[stage], "symbol": symbol, "stage": stage,
            "evidence": evidence, **verdict}


# Human-readable one-liners per stage (shown as the headline in the UI).
_STAGE_LABELS = {
    "NOT_TRADABLE": {"stage_label": "Outside the tradable universe"},
    "TAKEN": {"stage_label": "We actually DID evaluate & approve this — check sizing/exit, not the miss"},
    "COMMITTEE_REJECT": {"stage_label": "The committee debated it in full and rejected it"},
    "TRIAGE_SKIP": {"stage_label": "Triage saw it as a candidate and passed"},
    "NO_TRACE": {"stage_label": "No record — the desk never evaluated it (likely a data/coverage gap)"},
}
