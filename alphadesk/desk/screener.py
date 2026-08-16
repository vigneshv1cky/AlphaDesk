"""The screener — "which stocks should I be looking at right now."

Two-stage, deliberately: a CODE-COMPUTED ranking decides WHICH symbols are
worth a human's attention (earnings proximity + news volume/recency — no AI,
same "informational score, never a decision" pattern as the rest of this
repo), then an AI digest explains WHY for only the top N. The AI never picks
the list; it narrates a list code already picked. That split means a
DeepSeek outage degrades to "here are the symbols and their raw headlines"
instead of an empty page.

No claim renders without a source: every digest citation carries the
article's URL (CLAUDE.md's attribution rule). The prompt is instructed to
cite by article index and the citations are resolved back to real URLs here,
not trusted from model output.
"""

import hashlib
import logging

from alphadesk.ai.deepseek import DeepSeekError, chat_json, wrap_data
from alphadesk.config import NEWS_LOOKBACK_HOURS, SCREENER_HORIZON_DAYS, SCREENER_TOP_N
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.screener")

_DIGEST_SYSTEM = (
    "You are a financial research assistant. Given recent news articles about "
    "ONE stock, write a 2-3 sentence digest of what's happening and why a "
    "trader might care right now. Be concrete (numbers, events, dates), not "
    "generic. Every factual claim MUST cite the article index it came from.\n"
    "Return ONLY JSON: {\"digest\": \"...\", "
    "\"citations\": [{\"article_index\": <1-based int>, \"claim\": \"short phrase\"}]}"
)


def _input_hash(article_ids: list[str]) -> str:
    return hashlib.sha1(",".join(sorted(article_ids)).encode()).hexdigest()[:16]


def _digest_for(symbol: str, articles: list[dict]) -> dict | None:
    """AI digest for one symbol's articles, cache-first on the exact article
    set. Returns {digest, citations: [{title,url,source,claim}]} or None on
    failure — the caller renders raw headlines instead, never a placeholder."""
    ids = [a["article_id"] for a in articles]
    h = _input_hash(ids)
    cached = store.get_digest(symbol, h)
    if cached:
        # Resolve stored index-citations back to live article objects — the
        # article order at generation time is fixed by article_ids' sort order.
        return _resolve_citations(cached["digest"], cached["citations"], articles)

    numbered = "\n".join(
        f"{i + 1}. [{a.get('source', '?')}] {a['title']}"
        + (f" — {a['summary'][:200]}" if a.get("summary") else "")
        for i, a in enumerate(articles)
    )
    try:
        out = chat_json(_DIGEST_SYSTEM, wrap_data("articles", numbered),
                        role="screener-digest", source="POLYGON", decision_id=symbol)
    except DeepSeekError as exc:
        log.warning("digest failed for %s: %s", symbol, exc)
        return None

    digest = (out.get("digest") or "").strip()
    citations = [{"article_index": int(c.get("article_index", 0)), "claim": c.get("claim", "")}
                 for c in (out.get("citations") or []) if c.get("article_index")]
    if not digest:
        return None
    store.save_digest(symbol, h, digest, citations, model="deepseek-chat")
    return _resolve_citations(digest, citations, articles)


def _resolve_citations(digest: str, citations: list[dict], articles: list[dict]) -> dict:
    """Turn {article_index, claim} into real {title, url, source, claim} —
    never trust the model's own idea of a URL, only its INDEX into the list
    we gave it, resolved against the record we control."""
    resolved = []
    for c in citations:
        idx = c.get("article_index", 0) - 1
        if 0 <= idx < len(articles):
            a = articles[idx]
            resolved.append({"claim": c.get("claim", ""), "title": a["title"],
                             "url": a.get("url", ""), "source": a.get("source", "")})
    return {"digest": digest, "citations": resolved}


def build(top_n: int = SCREENER_TOP_N) -> list[dict]:
    """The ranked list. Each entry:
    {symbol, score, report_date, session, article_count, digest, citations,
     headlines: [{title,url,source,published_at}]}   (headlines always present;
     digest/citations present only where the AI call succeeded)
    """
    from alphadesk.config import now_et

    upcoming = store.upcoming_earnings(days=SCREENER_HORIZON_DAYS)
    by_symbol = {r["symbol"].upper(): r for r in upcoming}

    since_iso = (now_et().astimezone(__import__("datetime").timezone.utc)
                 - __import__("datetime").timedelta(hours=NEWS_LOOKBACK_HOURS)).isoformat()
    news_by_symbol = store.recent_articles_by_ticker(since_iso)

    # Union of "reporting soon" and "has fresh news" — either alone earns a look.
    candidates = set(by_symbol) | set(news_by_symbol)

    scored = []
    for sym in candidates:
        earn = by_symbol.get(sym)
        arts = news_by_symbol.get(sym, [])
        days_out = None
        if earn and earn.get("report_date"):
            try:
                rd = __import__("datetime").date.fromisoformat(earn["report_date"])
                days_out = (rd - now_et().date()).days
            except ValueError:
                pass
        # Deterministic score: closer earnings + more/fresher news = higher.
        # Purely a SORT key for what gets an (expensive) AI digest — see the
        # module docstring, this never gates a trade.
        score = 0.0
        if days_out is not None:
            score += max(0.0, 10 - abs(days_out)) * 3
        score += min(len(arts), 8) * 2
        if arts:
            score += 5   # any fresh news outranks stale earnings-only entries
        scored.append({"symbol": sym, "score": round(score, 1), "earn": earn, "articles": arts})

    scored.sort(key=lambda r: -r["score"])
    top = scored[:top_n]

    out = []
    for row in top:
        arts = row["articles"]
        digest_block = _digest_for(row["symbol"], arts) if arts else None
        out.append({
            "symbol": row["symbol"],
            "score": row["score"],
            "report_date": (row["earn"] or {}).get("report_date"),
            "session": (row["earn"] or {}).get("session"),
            "article_count": len(arts),
            "digest": (digest_block or {}).get("digest"),
            "citations": (digest_block or {}).get("citations") or [],
            "headlines": [{"title": a["title"], "url": a.get("url", ""),
                          "source": a.get("source", ""), "published_at": a.get("published_at")}
                         for a in arts[:5]],
        })
    return out
