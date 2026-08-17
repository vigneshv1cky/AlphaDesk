"""Research over one symbol — pre-fetches fundamentals, institutional
ownership, insider trades, earnings history, macro conditions, and sector
performance, then a SINGLE DeepSeek call synthesizes an answer (and a brief
suggestion) from exactly that data.

Same shape as desk/filings.py: the server decides what data is relevant
(here, a fixed set of sections for the given symbol, not a caller-supplied
document) and hands it all to one chat_json() call — no tool-calling loop.
Citations resolve by SECTION INDEX against the real sections this module
fetched, generalizing desk/screener.py's index-into-a-controlled-list
pattern from articles to data sections — the model's own claim about what a
section contains is never trusted past that check.
"""

import hashlib
import json
import logging

from alphadesk.ai.deepseek import DeepSeekError, chat_json, wrap_data
from alphadesk.config import RESEARCH_CACHE_TTL_HOURS, RESEARCH_MAX_CHARS
from alphadesk.ingest import openbb_ownership, prices
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.research")

_SYSTEM = (
    "You are a research assistant. Answer the question about the given "
    "symbol using ONLY the data sections provided below — never guess or "
    "use outside/training knowledge (this matters most for macro/rate "
    "claims, where a stale training-data answer is worse than none). If the "
    "provided data can't answer the question, say so plainly. End with a "
    "short, clearly-labeled suggestion or takeaway, still grounded only in "
    "the data given.\n"
    "Every factual claim must cite which section backed it.\n"
    "Return ONLY JSON: {\"answer\": \"...\", "
    "\"citations\": [{\"section\": <1-based section number>, \"claim\": \"...\"}]}"
)


def _clean_symbol(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    sym = "".join(c for c in raw.upper() if c.isalnum() or c in ".-")[:12]
    return sym or None


def _wrap_insider_trades(rows: list[dict]) -> list[dict]:
    wrapped = []
    for row in rows:
        row = dict(row)
        # footnote is the one genuinely free-narrative field here (attorney/
        # company-drafted text) — company_name/owner_name/owner_title are
        # short structured strings off the filing's XML, not prose.
        footnote = row.get("footnote")
        if isinstance(footnote, str) and footnote:
            row["footnote"] = wrap_data("insider_footnote", footnote)
        wrapped.append(row)
    return wrapped


def _fetch_sections(symbol: str) -> list[dict]:
    """Every section a symbol question might need, fetched up front. Each:
    {title, data} — data is None/unavailable-shaped when that source failed,
    never an exception the caller has to handle."""
    fundamentals = prices.get_fundamentals(symbol)
    sector = fundamentals.get("sector") if fundamentals else None

    insider = openbb_ownership.get_insider_trades(symbol)
    sector_perf = prices.sector_change_pct(sector) if sector else None

    return [
        {"title": "Fundamentals", "data": fundamentals or {"available": False}},
        {"title": "Institutional ownership",
         "data": prices.get_institutional_ownership(symbol) or {"available": False}},
        {"title": "Insider trades (SEC Form 4)",
         "data": {"trades": _wrap_insider_trades(insider)} if insider else {"available": False}},
        {"title": "Earnings history",
         "data": prices.get_earnings_context(symbol) or {"available": False}},
        {"title": "Macro snapshot",
         "data": prices.macro_snapshot() or {"available": False}},
        {"title": "Sector performance",
         "data": {"sector": sector, "change_pct": sector_perf} if sector_perf is not None
                 else {"available": False}},
    ]


def _qhash(question: str) -> str:
    return hashlib.sha1(question.strip().lower().encode()).hexdigest()[:16]


def _resolve_citations(citations: list[dict], sections: list[dict]) -> list[dict]:
    """Keep only citations pointing at a real section that actually has data
    — the model's own section number is never trusted past this check, same
    discipline as filings._verify_quotes/screener._resolve_citations. A
    citation pointing at an unavailable section is dropped, not shown."""
    out = []
    for c in citations:
        n = c.get("section")
        claim = c.get("claim")
        if not isinstance(n, int) or not isinstance(claim, str) or not claim.strip():
            continue
        i = n - 1
        if i < 0 or i >= len(sections):
            continue
        data = sections[i]["data"]
        if isinstance(data, dict) and data.get("available") is False:
            continue
        out.append({"section": n, "title": sections[i]["title"], "claim": claim.strip()})
    return out


def ask(symbol: str, question: str) -> dict | None:
    """{answer, citations: [{section, title, claim}], sections} or None if
    the symbol/question is invalid, nothing usable could be fetched, or the
    model call fails — the caller shows 'try again', never a fabricated
    answer. Cached per (symbol, question) with a TTL (the underlying data
    can go stale even when the question hasn't changed)."""
    sym = _clean_symbol(symbol)
    question = question.strip()
    if not sym or not question:
        return None

    qh = _qhash(question)
    cached = store.get_research(sym, qh, RESEARCH_CACHE_TTL_HOURS)
    if cached:
        return {"answer": cached["answer"], "citations": cached["citations"], "sections": cached["sections"]}

    sections = _fetch_sections(sym)
    if not any(not (isinstance(s["data"], dict) and s["data"].get("available") is False) for s in sections):
        log.warning("no usable research data for %s", sym)
        return None

    user_parts = [f"Symbol: {sym}", f"Question: {question}", ""]
    for i, s in enumerate(sections, start=1):
        user_parts.append(wrap_data(f"section_{i}", f"Section {i} — {s['title']}:\n{json.dumps(s['data'], default=str)}"))
    user = "\n\n".join(user_parts)

    try:
        out = chat_json(
            _SYSTEM, user, role="research-agent", source=None, decision_id=f"{sym}:{qh}",
            max_input_chars=RESEARCH_MAX_CHARS, max_tokens=1024,
        )
    except DeepSeekError as exc:
        log.warning("research agent failed for %s %r: %s", sym, question, exc)
        return None

    answer = (out.get("answer") or "").strip()
    if not answer:
        return None
    raw_citations = [c for c in (out.get("citations") or []) if isinstance(c, dict)]
    citations = _resolve_citations(raw_citations, sections)

    store.save_research(sym, qh, question, answer, citations, sections, "deepseek-chat")
    return {"answer": answer, "citations": citations, "sections": sections}
