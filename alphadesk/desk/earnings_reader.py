"""Earnings evidence — code-fetched FACTS only, no LLM anywhere in the number path.

The earnings brief the team debates from is assembled in CODE from the calendar
row + `prices.get_earnings_context` (beat/miss track record, quarterly revenue/
income trend, analyst estimate trajectory + revisions — post-report revisions are
the drift mechanism, and they're the quantified form of what guidance prose gives).
Faster, free, and incapable of confabulating figures — the failure class this
replaced (an ungrounded model inventing guidance numbers, e.g. COCO's fabricated
"$580-590M" and TSLA's dueling "-87.5% vs -19% vs real -38.35%" surprises).
"""

import logging

log = logging.getLogger("alphadesk.earnings_reader")


def earnings_block(row: dict, decision_id: str | None = None) -> dict:
    """The earnings brief for the debate — pure code-assembled FACTS.
    row: the calendar row (symbol, report_date, session, eps_estimate/actual,
    surprise_pct). Returns {"kind": "earnings", "summary", "key_facts"}."""
    from alphadesk.ingest import prices
    sym = row["symbol"]
    est, act, surp = row.get("eps_estimate"), row.get("eps_actual"), row.get("surprise_pct")
    if surp is not None:
        verdict = "beat" if surp > 0 else ("miss" if surp < 0 else "in-line")
        head = (f"{sym} reported {row.get('report_date', '')[:10]} ({row.get('session') or ''}): "
                f"EPS {act} vs est {est} — {verdict} ({surp:+.1f}%)")
    else:
        head = (f"{sym} reported {row.get('report_date', '')[:10]} ({row.get('session') or ''}): "
                f"EPS est {est} — actual not yet released (drift from the price reaction)")

    facts: list[str] = []
    ctx = prices.get_earnings_context(sym) or {}
    hist = ctx.get("report_history") or []
    if hist:
        facts.append(f"Track record: {ctx.get('beat_streak')} in the last {len(hist)} quarters "
                     "(" + "; ".join(f"{h['date']}: {h['surprise_pct']:+}%" for h in hist[:4]) + ")")
    if ctx.get("revenue_qoq_pct") is not None:
        facts.append(f"Revenue {ctx['revenue_qoq_pct']:+.1f}% QoQ "
                     f"(last 4 quarters, $B: {ctx.get('revenue_last4_bn')})")
    rev = ctx.get("revisions_30d")
    if rev and (rev["up"] or rev["down"]):
        line = f"Analyst revisions (30d): {rev['up']} up / {rev['down']} down"
        if ctx.get("estimate_30d_change_pct") is not None:
            line += f"; next-Q EPS estimate moved {ctx['estimate_30d_change_pct']:+.1f}% in 30d"
        facts.append(line)

    return {"kind": "earnings",
            "summary": head + (" — " + " | ".join(facts) if facts else ""),
            "key_facts": [{"fact": f} for f in facts[:5]]}
