"""Autonomous tool-calling research agent — the model decides what to fetch
(fundamentals, institutional ownership, insider trades, earnings history,
macro conditions, sector performance) across multiple turns to answer a
free-form question.

Every claim in the answer cites a REAL tool call captured server-side
(ai.deepseek.run_tool_loop's `trace`) — the model's own say-so is never
trusted, same "no claim without a source" discipline as desk/filings.py
(verbatim-quote verification) and desk/screener.py (index-into-a-controlled-
list resolution), generalized here to "index into a controlled list of real
tool calls" instead of article citations or document quotes.

Question-only, not (symbol, question) like filings.ask() — unlike a filing
(naturally document-scoped), a research question may be pure macro ("is the
Fed likely to cut") or span symbols the model itself has to identify from the
question text, the same way a human reading it would.
"""

import hashlib
import logging

from alphadesk.ai.deepseek import DeepSeekError, run_tool_loop, wrap_data
from alphadesk.config import RESEARCH_CACHE_TTL_HOURS, RESEARCH_MODEL
from alphadesk.ingest import openbb_ownership, prices
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.research")

_SYSTEM = (
    "You are a research assistant answering questions about stocks and "
    "markets using ONLY the tools provided. Never guess or use outside/"
    "training knowledge — if the tools can't answer the question, say so "
    "plainly rather than filling the gap from memory (this matters most for "
    "macro/rate questions, where a stale training-data answer is worse than "
    "no answer). Call a tool for every fact you use; call provide_answer "
    "only once you have enough, and cite the tool_call_index of a real call "
    "for every claim."
)


def _clean_symbol(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    sym = "".join(c for c in raw.upper() if c.isalnum() or c in ".-")[:12]
    return sym or None


def _exec_get_fundamentals(args: dict) -> dict:
    sym = _clean_symbol(args.get("symbol"))
    if not sym:
        return {"error": "symbol required"}
    data = prices.get_fundamentals(sym)
    return data if data else {"available": False, "symbol": sym}


def _exec_get_institutional_ownership(args: dict) -> dict:
    sym = _clean_symbol(args.get("symbol"))
    if not sym:
        return {"error": "symbol required"}
    data = prices.get_institutional_ownership(sym)
    return data if data else {"available": False, "symbol": sym}


def _exec_get_insider_trades(args: dict) -> dict:
    sym = _clean_symbol(args.get("symbol"))
    if not sym:
        return {"error": "symbol required"}
    rows = openbb_ownership.get_insider_trades(sym)
    if not rows:
        return {"available": False, "symbol": sym}
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
    return {"symbol": sym, "trades": wrapped}


def _exec_get_earnings_context(args: dict) -> dict:
    sym = _clean_symbol(args.get("symbol"))
    if not sym:
        return {"error": "symbol required"}
    data = prices.get_earnings_context(sym)
    return data if data else {"available": False, "symbol": sym}


def _exec_get_macro_snapshot(_args: dict) -> dict:
    data = prices.macro_snapshot()
    return data if data else {"available": False}


def _exec_get_sector_performance(args: dict) -> dict:
    sector = args.get("sector")
    if not isinstance(sector, str) or not sector:
        return {"error": "sector required"}
    pct = prices.sector_change_pct(sector)
    if pct is None:
        return {"available": False, "sector": sector}
    return {"sector": sector, "change_pct": pct}


_EXECUTORS = {
    "get_fundamentals": _exec_get_fundamentals,
    "get_institutional_ownership": _exec_get_institutional_ownership,
    "get_insider_trades": _exec_get_insider_trades,
    "get_earnings_context": _exec_get_earnings_context,
    "get_macro_snapshot": _exec_get_macro_snapshot,
    "get_sector_performance": _exec_get_sector_performance,
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


_TOOLS = [
    _tool("get_fundamentals",
         "Valuation/quality facts: market cap, trailing/forward P/E, profit "
         "margin, revenue growth, sector, industry, short interest.",
         {"symbol": {"type": "string"}}, ["symbol"]),
    _tool("get_institutional_ownership",
         "Institutional/major-holder breakdown: top holders (e.g. BlackRock, "
         "Vanguard) and % of shares held by institutions/insiders.",
         {"symbol": {"type": "string"}}, ["symbol"]),
    _tool("get_insider_trades",
         "Recent SEC Form 4 insider buy/sell transactions — officer/director "
         "name, transaction type, price, shares, filing URL.",
         {"symbol": {"type": "string"}}, ["symbol"]),
    _tool("get_earnings_context",
         "Beat/miss track record (last 4 quarters), revenue/income trend, "
         "analyst estimate revisions.",
         {"symbol": {"type": "string"}}, ["symbol"]),
    _tool("get_macro_snapshot",
         "10-year Treasury yield, VIX, and a Fed-funds proxy, each with a "
         "1-month-ago comparison. Takes no arguments.",
         {}, []),
    _tool("get_sector_performance",
         "Today's % change for a GICS sector's tracking ETF (pass the exact "
         "sector name from get_fundamentals' result, e.g. \"Technology\").",
         {"sector": {"type": "string"}}, ["sector"]),
]


def _dispatch(name: str, args: dict) -> dict:
    executor = _EXECUTORS.get(name)
    if executor is None:
        return {"error": f"unknown tool {name!r}"}
    return executor(args)


def _qhash(question: str) -> str:
    return hashlib.sha1(question.strip().lower().encode()).hexdigest()[:16]


def _resolve_citations(citations: list[dict], trace: list[dict]) -> list[dict]:
    """Keep only citations pointing at a real, successfully-executed tool
    call — the model's own tool_call_index is never trusted past this check,
    same discipline as filings._verify_quotes/screener._resolve_citations. A
    citation whose call errored or came back unavailable is dropped, not
    shown as "unverified"."""
    out = []
    for c in citations:
        idx = c.get("tool_call_index")
        claim = c.get("claim")
        if not isinstance(idx, int) or not isinstance(claim, str) or not claim.strip():
            continue
        if idx < 0 or idx >= len(trace):
            continue
        call = trace[idx]
        result = call.get("result")
        if isinstance(result, dict) and (result.get("error") or result.get("available") is False):
            continue
        out.append({"tool_call_index": idx, "claim": claim.strip(),
                    "tool": call.get("tool"), "args": call.get("args")})
    return out


def ask(question: str) -> dict | None:
    """{answer, citations: [{tool_call_index, claim, tool, args}], trace}
    or None if the loop fails, times out, or never grounds an answer in a
    real tool call — the caller shows 'try again', never a fabricated
    answer. Cached on the question text alone with a TTL (not a hash-of-
    inputs cache like symbol_digests/filing_qa_cache — see research_cache's
    schema comment for why: the model decides what to fetch at ask-time, so
    there's no input set to hash)."""
    question = question.strip()
    if not question:
        return None
    qh = _qhash(question)
    cached = store.get_research(qh, RESEARCH_CACHE_TTL_HOURS)
    if cached:
        return {"answer": cached["answer"], "citations": cached["citations"], "trace": cached["trace"]}

    try:
        result = run_tool_loop(
            _SYSTEM, question, _TOOLS, _dispatch,
            role="research-agent", source=None, decision_id=qh, model=RESEARCH_MODEL,
        )
    except DeepSeekError as exc:
        log.warning("research agent failed for %r: %s", question, exc)
        return None

    citations = _resolve_citations(result["citations"], result["trace"])
    store.save_research(qh, question, result["answer"], citations, result["trace"], RESEARCH_MODEL)
    return {"answer": result["answer"], "citations": citations, "trace": result["trace"]}
