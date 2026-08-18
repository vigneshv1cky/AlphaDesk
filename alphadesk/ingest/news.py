"""News ingestion — poll the configured provider, enrich, persist.

Recovered and adapted from the v1 multi-agent system (removed 11263ae,
2026-08-07): same Polygon fetch and the same enrichment_cache-backed
amortization, with the LLM call swapped from the old committee's call_role()
(claude_sdk/kimi/deepseek, multi-role) to this repo's single-purpose
ai/llm.py client. The enrichment prompt (category/sentiment/relations) is
unchanged — it was already tuned and working.
"""

import logging
from datetime import datetime

from alphadesk.ai.llm import LLMError, chat_json, wrap_data
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.news")

_BATCH = 30               # articles per enrichment call — fewer calls, less overhead
_seen_ids: set[str] = set()
_SEEN_CAP = 100_000       # bound memory in a 24/7 process; clearing only risks
                          # re-fetching an old article, absorbed by enrichment_cache

_ENRICH_SYSTEM = (
    "You are a financial news enrichment engine. For each numbered article you "
    "receive, produce a substance category, sentiment, and any explicit "
    "inter-company relations stated in the text.\n"
    "category — what KIND of information this is:\n"
    "  BUSINESS_EVENT: something happened at the company — earnings/guidance, "
    "M&A, contracts, products, leadership, legal/regulatory action against it\n"
    "  SUPPLY_DEMAND: supply-chain, production, capacity, shortages, pricing "
    "power, demand signals, orders, inventory\n"
    "  MACRO_POLICY: rates, regulation, tariffs, geopolitics affecting sectors\n"
    "  PRICE_COMMENTARY: the article mainly narrates stock-price action "
    "('X soared/plunged/hit a high', 'why X stock moved', weekly recaps)\n"
    "  OPINION: listicles, 'top N stocks to buy', 'should you buy X', "
    "evergreen takes with no new information\n"
    "sentiment: -1.0 (very negative) to 1.0 (very positive) — the OVERALL tone. "
    "label: negative|neutral|positive.\n"
    "ticker_sentiment: when the article names MULTIPLE companies and the news is "
    "NOT symmetric across them, give the per-company sentiment (e.g. 'X sues Y' is "
    "negative for Y but neutral/positive for X). List ONLY tickers whose sentiment "
    "differs from the overall — any ticker you omit inherits the article sentiment. "
    "Skip this entirely for single-company or uniformly-toned articles.\n"
    "relations: ONLY relations explicitly stated or strongly implied by the "
    "article text itself (e.g. 'X supplies chips to Y', 'X competes with Y').\n"
    "Return ONLY JSON: {\"items\": [{\"i\": <1-based index>, \"category\": ..., "
    "\"sentiment\": ..., \"label\": ..., "
    "\"ticker_sentiment\": [{\"t\": \"TICK\", \"sentiment\": ..., \"label\": ...}], "
    "\"relations\": [{\"a\": \"TICK\", \"rel\": \"...\", \"b\": \"TICK\"}]}]}"
)


def _provider_name() -> str:
    """Which feed this batch came from — recorded against the token spend so
    /api/tokens stays meaningful after someone switches providers."""
    from alphadesk.providers import get_news
    try:
        return getattr(get_news(), "name", "news")
    except Exception:
        return "news"


def fetch_articles(since: datetime, limit: int = 200) -> list[dict]:
    """Ticker-tagged articles since `since`, NEWEST first, from whichever news
    provider is configured (NEWS_PROVIDER).

    Returns the dict shape the rest of the pipeline stores. A provider failure
    is logged and yields an empty list: a dead feed must leave the terminal
    showing the articles it already has, never an error page.
    """
    from alphadesk.providers import ProviderError, get_news
    try:
        provider = get_news()
        articles = provider.fetch(since, limit=limit)
    except ProviderError as exc:
        log.warning("news provider unavailable: %s", exc)
        return []
    except Exception as exc:                      # a broken plugin is not fatal
        log.error("news provider raised unexpectedly: %s", exc)
        return []

    out: list[dict] = []
    for a in articles:
        # Dedupe across polls. The window overlaps by design, so without this
        # every cycle would re-enrich the same articles.
        if not a.id or a.id in _seen_ids:
            continue
        if len(_seen_ids) >= _SEEN_CAP:
            _seen_ids.clear()
        _seen_ids.add(a.id)
        out.append({
            "id": a.id, "title": a.title, "summary": a.summary,
            "source": a.source, "url": a.url,
            "published_at": a.published_at, "tickers": a.symbols,
        })
    return out


def enrich(articles: list[dict]) -> dict[str, dict]:
    """Attach sentiment/label/relations to each article, reusing enrichment_cache
    so overlapping news is never re-enriched across runs/restarts.

    On LLM failure a batch falls back to neutral/UNCLASSIFIED (stays visible
    rather than silently dropped) and is NOT cached, so it gets a real
    enrichment next run."""
    cached = store.get_enrichment([a["id"] for a in articles])
    to_enrich = [a for a in articles if a["id"] not in cached]
    fresh: dict[str, dict] = dict(cached)
    cacheable: list[dict] = []

    def _enrich_batch(batch: list[dict]) -> dict[int, dict]:
        numbered = "\n".join(
            f"{i + 1}. [{', '.join(a['tickers'])}] {a['title']}"
            + (f" — {a['summary'][:200]}" if a["summary"] else "")
            for i, a in enumerate(batch)
        )
        try:
            out = chat_json(_ENRICH_SYSTEM, "Articles:\n" + wrap_data("articles", numbered),
                            role="news-enrich", source=_provider_name())
            return {item["i"]: item for item in out.get("items", [])}
        except LLMError as exc:
            log.warning("Enrichment batch failed (%s) — neutral fallback ×%d", exc, len(batch))
            return {}

    batches = [to_enrich[s:s + _BATCH] for s in range(0, len(to_enrich), _BATCH)]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        batch_results = list(pool.map(_enrich_batch, batches))

    for batch, results in zip(batches, batch_results):
        for i, art in enumerate(batch):
            item = results.get(i + 1)
            rec = {
                "sentiment": float((item or {}).get("sentiment", 0.0)),
                "label": (item or {}).get("label", "neutral"),
                "category": (item or {}).get("category", "UNCLASSIFIED"),
                "relations": [{"a": r["a"], "rel": r["rel"], "b": r["b"]}
                              for r in ((item or {}).get("relations") or [])],
                "ticker_sentiment": {
                    (ts.get("t") or "").upper(): {
                        "sentiment": float(ts.get("sentiment", 0.0)),
                        "label": ts.get("label", "neutral"),
                    }
                    for ts in ((item or {}).get("ticker_sentiment") or []) if ts.get("t")
                },
            }
            fresh[art["id"]] = rec
            if item is not None:
                cacheable.append({"article_id": art["id"], **rec})

    store.save_enrichment(cacheable)
    return fresh


def poll(since: datetime, limit: int = 200) -> int:
    """One full cycle: fetch → persist raw → enrich → cache. Returns the
    number of NEW articles ingested (not the total scanned)."""
    articles = fetch_articles(since, limit)
    if not articles:
        return 0
    store.save_articles(articles)
    enrich(articles)
    return len(articles)
