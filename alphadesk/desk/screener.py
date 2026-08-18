"""The screener — an INVENTORY of what's in play, plus an AI you ask.

Two deliberate properties, and the second is why the first exists:

  1. **Nothing is ranked.** `inventory()` returns every symbol with fresh news
     or a report inside `SCREENER_HORIZON_DAYS`, in alphabetical order. There
     is no score, no top-N, no "these 15 matter most". An earlier build ranked
     by earnings proximity + news volume and auto-narrated the top of that
     list; ordering a list IS a judgment, and this terminal's whole premise is
     that the judgment is the operator's.
  2. **The AI speaks only when asked.** `ask()` runs ONE chat_json() call over
     the WHOLE window — every article and every upcoming report at once — and
     answers the question actually posed. Nothing is generated in the
     background, so a page load costs zero tokens and the model never
     pre-decides what was interesting.

No claim renders without a source, same discipline as the rest of the repo
(CLAUDE.md's attribution rule). Everything the model is shown is a NUMBERED
item drawn from our own tables — an article or a calendar row — and it cites
by that index. `_resolve_citations` maps the index back to the stored record;
the model's own idea of a URL or a date is never trusted, only its index into
a list we control.
"""

import hashlib
import logging
from datetime import timedelta, timezone

from alphadesk.ai.llm import LLMError, chat_json, wrap_data
from alphadesk.config import (
    NEWS_LOOKBACK_HOURS,
    SCREENER_ASK_MAX_ARTICLES,
    SCREENER_ASK_MAX_CHARS,
    SCREENER_HORIZON_DAYS,
    now_et,
)
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.screener")

# symbol_digests is (symbol, input_hash) -> answer + citations, which is
# exactly this cache's shape. A market-wide ask has no one symbol, so it is
# stored under a sentinel that can never collide with a real ticker (store
# uppercases symbols; no ticker contains '*').
_ASK_CACHE_SYMBOL = "*SCREENER-ASK*"

_ASK_SYSTEM = (
    "You are a financial research assistant reading a trader's whole watch "
    "window at once: recent news articles and upcoming earnings dates across "
    "many symbols. Answer the question actually asked, using ONLY the "
    "numbered items provided — never guess or use outside knowledge.\n"
    "Be concrete (symbols, numbers, dates). If the items can't answer the "
    "question, say so plainly rather than padding. Do not rank or recommend "
    "trades unless the question explicitly asks you to.\n"
    "Every factual claim MUST cite the item number it came from.\n"
    "Return ONLY JSON: {\"answer\": \"...\", "
    "\"citations\": [{\"item\": <1-based int>, \"claim\": \"short phrase\"}]}"
)


def _since_iso() -> str:
    return (now_et().astimezone(timezone.utc)
            - timedelta(hours=NEWS_LOOKBACK_HOURS)).isoformat()


def inventory() -> list[dict]:
    """Everything in the window, UNRANKED and alphabetical. Each entry:
    {symbol, report_date, session, article_count,
     headlines: [{title, url, source, published_at}]}

    Pure database read — no LLM call, so this is fast and free however many
    symbols are in play.
    """
    upcoming = store.upcoming_earnings(days=SCREENER_HORIZON_DAYS)
    by_symbol = {r["symbol"].upper(): r for r in upcoming}
    news_by_symbol = store.recent_articles_by_ticker(_since_iso())

    out = []
    # Alphabetical, not by any measure of interest: the order of this list is
    # not a recommendation. Stable across polls, too — a re-sort under the
    # cursor every 60s would itself read as "this one moved up".
    for sym in sorted(set(by_symbol) | set(news_by_symbol)):
        earn = by_symbol.get(sym) or {}
        arts = news_by_symbol.get(sym, [])
        out.append({
            "symbol": sym,
            "report_date": earn.get("report_date"),
            "session": earn.get("session"),
            "article_count": len(arts),
            "headlines": [{"title": a["title"], "url": a.get("url", ""),
                           "source": a.get("source", ""),
                           "published_at": a.get("published_at")}
                          for a in arts[:5]],
        })
    return out


def _collect_items() -> list[dict]:
    """The citable universe for one ask: upcoming reports first (few, and
    they date the window), then articles newest-first, capped at
    SCREENER_ASK_MAX_ARTICLES. ONE index space over both kinds, so every
    claim — 'MSFT reports Thursday' as much as 'Reuters says X' — resolves to
    a record this server stored."""
    items: list[dict] = []
    for e in store.upcoming_earnings(days=SCREENER_HORIZON_DAYS):
        items.append({"kind": "earnings", "symbol": e["symbol"].upper(),
                      "report_date": e.get("report_date"),
                      "session": e.get("session")})

    seen: set[str] = set()
    articles: list[dict] = []
    for sym, arts in store.recent_articles_by_ticker(_since_iso()).items():
        for a in arts:
            if a["article_id"] in seen:
                continue          # one article can be tagged with many tickers
            seen.add(a["article_id"])
            articles.append({"kind": "article", "symbol": sym, **a})
    # Newest first, so the cap sacrifices the OLDEST news — same policy as
    # ingest/news.py's scan cap, for the same reason.
    articles.sort(key=lambda a: a.get("published_at") or "", reverse=True)
    items.extend(articles[:SCREENER_ASK_MAX_ARTICLES])
    return items


def _render(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items, start=1):
        if it["kind"] == "earnings":
            when = it.get("report_date") or "?"
            lines.append(f"{i}. [EARNINGS] {it['symbol']} reports {when}")
        else:
            line = f"{i}. [NEWS] {it['symbol']} — [{it.get('source') or '?'}] {it['title']}"
            if it.get("summary"):
                line += f" — {it['summary'][:200]}"
            lines.append(line)
    return "\n".join(lines)


def _input_hash(question: str, items: list[dict]) -> str:
    ids = [it["article_id"] if it["kind"] == "article"
           else f"E:{it['symbol']}:{it.get('report_date')}" for it in items]
    payload = question.strip().lower() + "|" + ",".join(sorted(ids))
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _resolve_citations(citations: list[dict], items: list[dict]) -> list[dict]:
    """Turn {item, claim} into the real record behind it. An index outside
    the list we handed the model is dropped, not shown."""
    out = []
    for c in citations:
        n = c.get("item")
        claim = c.get("claim") or ""
        if not isinstance(n, int):
            continue
        i = n - 1
        if i < 0 or i >= len(items):
            continue
        it = items[i]
        if it["kind"] == "earnings":
            out.append({"kind": "earnings", "claim": claim, "symbol": it["symbol"],
                        "title": f"{it['symbol']} reports {it.get('report_date') or '?'}",
                        "url": "", "source": "earnings calendar"})
        else:
            out.append({"kind": "article", "claim": claim, "symbol": it["symbol"],
                        "title": it["title"], "url": it.get("url", ""),
                        "source": it.get("source", "")})
    return out


def ask(question: str) -> dict | None:
    """Answer one question over the ENTIRE current window in a single call.

    Returns {answer, citations: [...], considered: {articles, earnings,
    symbols}} or None if there's nothing in the window or the model call
    fails — the caller shows 'try again', never a fabricated answer.

    Cached per (question, exact item set): re-asking the same question while
    no new news has landed is free; a rephrase, or the same question after
    the window moves, is a deliberate miss.
    """
    question = question.strip()
    if not question:
        return None

    items = _collect_items()
    if not items:
        return None

    considered = {
        "articles": sum(1 for i in items if i["kind"] == "article"),
        "earnings": sum(1 for i in items if i["kind"] == "earnings"),
        "symbols": len({i["symbol"] for i in items}),
    }
    h = _input_hash(question, items)
    cached = store.get_digest(_ASK_CACHE_SYMBOL, h)
    if cached:
        return {"answer": cached["digest"], "citations": cached["citations"],
                "considered": considered}

    try:
        out = chat_json(
            _ASK_SYSTEM,
            f"Question: {question}\n\nItems:\n" + wrap_data("window", _render(items)),
            role="screener-ask", source="POLYGON",
            max_input_chars=SCREENER_ASK_MAX_CHARS, max_tokens=1536,
        )
    except LLMError as exc:
        log.warning("screener ask failed (%r): %s", question, exc)
        return None

    answer = (out.get("answer") or "").strip()
    if not answer:
        return None
    raw = [c for c in (out.get("citations") or []) if isinstance(c, dict)]
    citations = _resolve_citations(raw, items)

    store.save_digest(_ASK_CACHE_SYMBOL, h, answer, citations, model="deepseek-chat")
    return {"answer": answer, "citations": citations, "considered": considered}
