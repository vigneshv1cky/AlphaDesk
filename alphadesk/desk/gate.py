"""Pre-debate catalyst gate — a cheap haiku screen that drops picks with NO real,
tradeable catalyst BEFORE the expensive researcher→critic→judge debate runs.

The debate kept spending opus to reject phantom setups: single uncorroborated
sources (one Motley Fool post), internal spillover HYPOTHESES with no external
news, pure price-action narration. This catches those upstream for near-zero
tokens.

Two rails keep it honest:
  • FAIL-OPEN — on any error or genuine uncertainty the pick PROCEEDS to the
    debate. The gate only drops when the catalyst is CLEARLY absent, so a real
    setup is never silently killed by a flaky screen.
  • Drops are recorded as SKIPS and graded forward (anti-survivorship), so the
    ledger reveals if the gate is too aggressive — dropping names that then move.

Thinness is NOT the gate's job — a real, dated, externally-sourced event passes
even if single-source; the DEBATE weighs how thin it is. The gate only asks: is
there a concrete external event here at all, or nothing to trade?
"""

import json
import logging

from alphadesk.llm import LLMError, call_role, wrap_data

log = logging.getLogger("alphadesk.gate")


def _source_tag(a: dict) -> str:
    """Surface the trust signal the gate keys on: confirmed report vs internal
    hypothesis vs a real external source."""
    cat = a.get("category")
    src = a.get("source", "?")
    if cat == "EARNINGS":
        return "CONFIRMED-EARNINGS-REPORT"
    if cat == "SPILLOVER" or src == "ExposureDesk":
        return "INTERNAL-HYPOTHESIS-no-external-news"
    return src or "?"


_SYSTEM = (
    "You are the catalyst gate on a trading research desk. Before the desk spends a "
    "full debate on a name, decide ONE thing: is there a REAL, TRADEABLE catalyst "
    "here, or nothing solid to trade?\n"
    "TRADEABLE (let it through) = a concrete, dated, real-world EVENT with external "
    "support: a confirmed earnings report, a filing, M&A, trial data, a signed "
    "contract/ruling, a product or guidance change — reported by an outside source. "
    "A CONFIRMED-EARNINGS-REPORT always counts. A real event passes EVEN IF "
    "single-source — judging thinness is the debate's job, not yours.\n"
    "NOT TRADEABLE (gate it out) = the ONLY support is one of:\n"
    "  • an INTERNAL-HYPOTHESIS with no external news naming the company (a "
    "supply-chain read-through the desk inferred itself);\n"
    "  • pure price-action narration with no stated cause;\n"
    "  • no concrete event at all — just an opinion, a listicle, or vague coverage.\n"
    "When genuinely unsure, answer tradeable=true (let the debate decide).\n"
    'Return ONLY JSON: {"tradeable": true|false, "reason": "<one sentence>"}'
)

_SCHEMA = {
    "tradeable": {"type": bool},
    "reason": {"type": str, "maxlen": 200},
}


def screen_catalyst(symbol: str, reason: str, edge_hint: str | None,
                    articles: list[dict], decision_id: str | None = None) -> dict:
    """Cheap catalyst check for one pick. Returns {tradeable: bool, reason: str}.
    Fail-open: returns tradeable=True on any LLM error."""
    lines = []
    for a in articles[:6]:
        lines.append(json.dumps({
            "title": (a.get("title") or "")[:160],
            "source": _source_tag(a),
            "category": a.get("category"),
        }))
    user = (
        f"Symbol: {symbol}\nScout edge: {edge_hint or '?'}\nScout reason: {reason}\n\n"
        "Candidate articles:\n" + wrap_data("articles", "\n".join(lines))
    )
    try:
        out = call_role("gate", _SYSTEM, user, schema=_SCHEMA, decision_id=decision_id)
        return {"tradeable": bool(out.get("tradeable", True)),
                "reason": out.get("reason", "") or ""}
    except LLMError as exc:
        log.warning("Gate check failed for %s (%s) — passing through to debate", symbol, exc)
        return {"tradeable": True, "reason": "gate error — debated anyway"}
