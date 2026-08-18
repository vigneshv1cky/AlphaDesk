# Writing a provider

AlphaDesk talks to the outside world through three seams: the **LLM**, the
**news feed**, and **market data**. Each is a `Protocol` in
`alphadesk/providers/base.py`. A provider is any object with the right shape —
you do not subclass anything, and a provider package need not import AlphaDesk
at all except to register itself.

## The contracts

```python
class LLMProvider(Protocol):
    name: str
    def chat_json(self, system: str, user: str, *,
                  max_tokens: int = 2048, timeout_s: float = 60.0) -> ChatResult: ...

class NewsProvider(Protocol):
    name: str
    def fetch(self, since: datetime, limit: int = 200) -> list[Article]: ...

class PriceProvider(Protocol):
    name: str
    def context(self, symbol) -> dict | None: ...
    def chart_series(self, symbol, days=2) -> dict | None: ...
    def fundamentals(self, symbol) -> dict | None: ...
    def institutional_ownership(self, symbol) -> dict | None: ...
    def earnings_context(self, symbol) -> dict | None: ...
    def macro(self) -> dict | None: ...
    def sector_change_pct(self, sector) -> float | None: ...
```

Raise `ProviderError` for anything that goes wrong. Callers catch it, drop the
item and log why — a provider failure degrades one feature, it never takes down
a page.

## A minimal news provider

```python
from datetime import datetime
from alphadesk.providers import Article, ProviderError, register

class MyNews:
    name = "mynews"

    def fetch(self, since: datetime, limit: int = 200) -> list[Article]:
        try:
            rows = my_client.headlines(after=since, count=limit)
        except Exception as exc:
            raise ProviderError(f"mynews failed: {exc}") from exc
        return [
            Article(
                id=str(r["id"]),
                title=r["headline"],
                url=r["link"],
                published_at=r["ts"],          # ISO 8601
                symbols=r["tickers"],          # REQUIRED — see below
                summary=r.get("teaser", ""),
                source=r.get("publisher", ""),
            )
            for r in rows
        ]

register("news", MyNews.name, MyNews)
```

Register the **factory**, not an instance: construction may read config or open
a client, and that should not happen for providers nobody selected.

### Two things a news provider must get right

**`symbols` is not optional.** The screener groups the entire window by ticker.
A feed that cannot say which symbols an article is about cannot back this app.

**Return newest first.** Callers apply a hard cap, so the only correct thing to
drop under that cap is the oldest news.

This is also why a per-symbol-only feed (yfinance, say) is a poor fit: covering
the window would take one request per symbol per poll, and you could only ever
find news for symbols you already track — which removes discovery, the point of
the window.

## Getting your provider loaded

Three ways, in increasing order of decoupling:

**1. Local module** — set `ALPHADESK_PLUGINS` to a comma-separated list of
import paths. Importing is enough, since the module registers itself.

```ini
ALPHADESK_PLUGINS=myplugins.news,myplugins.llm
NEWS_PROVIDER=mynews
```

**2. Entry point** — a published package is discovered automatically once
installed, with no config:

```toml
[project.entry-points."alphadesk.providers"]
mynews = "mypackage.providers:register_all"
```

**3. Fork** — add it to `alphadesk/providers/` and register it in `builtin.py`.

Built-ins load first, so registering an existing name deliberately **replaces**
it: `register("news", "polygon", MyBetterPolygon)` overrides the shipped one
without patching this repo.

## Selecting one

```ini
LLM_PROVIDER=openai-compatible     # or anthropic, or yours
NEWS_PROVIDER=polygon              # or alpaca, or yours
PRICE_PROVIDER=builtin
```

`/api/system` reports what is registered and what is selected, which is usually
the fastest answer to "why is there no news".

## Shipped providers

| Kind | Name | Notes |
|---|---|---|
| llm | `openai-compatible` | DeepSeek, OpenAI, Groq, Together, LM Studio, Ollama — set `LLM_BASE_URL` + `LLM_MODEL` |
| llm | `anthropic` | Messages API; JSON is forced by prefilling the assistant turn |
| news | `polygon` | Firehose, ticker-tagged, broad publisher mix. Paid. |
| news | `alpaca` | Firehose, ticker-tagged, Benzinga-sourced. Bundled with Alpaca market data. |
| prices | `builtin` | Alpaca (quotes, intraday bars) + yfinance (fundamentals, ownership, macro) |

## Testing yours

`isinstance` works against these Protocols, so the cheapest possible test is:

```python
from alphadesk.providers import base
def test_shape():
    assert isinstance(MyNews(), base.NewsProvider)
```

See `tests/test_providers.py` for how the built-ins are covered.
