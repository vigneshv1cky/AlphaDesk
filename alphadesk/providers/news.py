"""News providers.

Two implementations, because one implementation never proves an interface.
Both return the same `Article` shape, so switching feeds is `NEWS_PROVIDER=`
and nothing else.

Both are FIREHOSE feeds: one request returns everything since a timestamp,
already tagged with the symbols each article is about. That shape is what the
screener needs — it groups the whole window by ticker. A per-symbol-only feed
(yfinance, for instance) cannot back this interface without N requests per
poll and would silently lose discovery, since you can only ask about symbols
you already know.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from alphadesk.providers.base import Article, ProviderError
from alphadesk.providers.registry import register

log = logging.getLogger("alphadesk.providers.news")

# Cap on RAW items paged through per call, distinct from the caller's `limit`
# on USABLE ones. Without it a wide window on a busy day pages forever.
_MAX_SCAN = int(os.environ.get("NEWS_MAX_SCAN", "400"))


class PolygonNews:
    """Polygon.io ticker news. Config: POLYGON_API_KEY."""

    name = "polygon"

    def __init__(self) -> None:
        self.api_key = os.environ.get("POLYGON_API_KEY", "").strip()

    def fetch(self, since: datetime, limit: int = 200) -> list[Article]:
        if not self.api_key:
            raise ProviderError("POLYGON_API_KEY is not set")
        try:
            import polygon
        except ImportError as exc:                    # pragma: no cover
            raise ProviderError("polygon-api-client is not installed") from exc

        client = polygon.RESTClient(api_key=self.api_key)
        out: list[Article] = []
        scanned = 0
        try:
            for art in client.list_ticker_news(
                published_utc_gte=since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                limit=min(limit, 1000), sort="published_utc", order="desc",
            ):
                scanned += 1
                if scanned > _MAX_SCAN:
                    log.warning("polygon scan cap (%d) hit at %d usable", _MAX_SCAN, len(out))
                    break
                symbols = [t for t in (getattr(art, "tickers", None) or []) if t]
                title = getattr(art, "title", "") or ""
                art_id = str(getattr(art, "id", "") or getattr(art, "article_url", ""))
                if not (art_id and title and symbols):
                    continue
                pub = getattr(art, "publisher", None)
                out.append(Article(
                    id=art_id,
                    title=title,
                    url=getattr(art, "article_url", "") or "",
                    published_at=str(getattr(art, "published_utc", "")
                                     or datetime.now(timezone.utc).isoformat()),
                    symbols=symbols[:8],
                    summary=(getattr(art, "description", "") or "")[:400],
                    source=pub.name if pub is not None and hasattr(pub, "name") else "Polygon",
                ))
                if len(out) >= limit:
                    break
        except Exception as exc:
            raise ProviderError(f"polygon fetch failed: {exc}") from exc
        return out


class AlpacaNews:
    """Alpaca market-data news (Benzinga-sourced).

    Config: ALPACA_API_KEY / ALPACA_SECRET_KEY — the same credentials the
    price provider already uses, so this costs nothing extra to enable.

    Narrower publisher mix than Polygon; the trade is that it is bundled with
    market data rather than separately billed.
    """

    name = "alpaca"

    def __init__(self) -> None:
        self.key = os.environ.get("ALPACA_API_KEY", "").strip()
        self.secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()

    def fetch(self, since: datetime, limit: int = 200) -> list[Article]:
        if not (self.key and self.secret):
            raise ProviderError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set")
        try:
            from alpaca.data.historical.news import NewsClient
            from alpaca.data.requests import NewsRequest
        except ImportError as exc:                    # pragma: no cover
            raise ProviderError("alpaca-py is not installed") from exc

        client = NewsClient(self.key, self.secret)
        out: list[Article] = []
        try:
            # Alpaca caps a page at 50; page until the caller's limit or the
            # raw-scan cap, whichever comes first.
            token, scanned = None, 0
            while len(out) < limit and scanned < _MAX_SCAN:
                req = NewsRequest(start=since, limit=50, sort="desc",
                                  exclude_contentless=True, page_token=token)
                page = client.get_news(req)
                items = getattr(page, "data", {}).get("news", []) if hasattr(page, "data") else []
                if not items:
                    break
                for a in items:
                    scanned += 1
                    symbols = [s for s in (getattr(a, "symbols", None) or []) if s]
                    title = getattr(a, "headline", "") or ""
                    art_id = str(getattr(a, "id", "") or getattr(a, "url", ""))
                    if not (art_id and title and symbols):
                        continue
                    ts = getattr(a, "created_at", None)
                    published = ts.isoformat() if isinstance(ts, datetime) else str(ts or "")
                    out.append(Article(
                        id=art_id,
                        title=title,
                        url=getattr(a, "url", "") or "",
                        published_at=published,
                        symbols=symbols[:8],
                        summary=(getattr(a, "summary", "") or "")[:400],
                        source=getattr(a, "source", "") or "Alpaca",
                    ))
                    if len(out) >= limit:
                        break
                token = getattr(page, "next_page_token", None)
                if not token:
                    break
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"alpaca news fetch failed: {exc}") from exc
        return out


register("news", PolygonNews.name, PolygonNews)
register("news", AlpacaNews.name, AlpacaNews)
