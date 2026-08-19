# AlphaDesk

A dense, dark, self-hosted **market research terminal**. Open source, and
pluggable at every seam that touches the outside world.

It reads: news, SEC filings, fundamentals, ownership, insider activity, the
earnings calendar, charts. It does not trade, hold positions or keep score —
see [Not a trading system](#not-a-trading-system).

```
┌ status ────────────────────────────────────────────────────────┬─────────┐
│ market · in window · with news · reporting · news today · up    │  ASK    │
├──────────────────┬─────────────────────────────────────────────┤         │
│ WINDOW           │ NEWS TAPE                                   │ window  │
│ 215 symbols      │ NVDA  Why the trade desk stock plunged…     │ symbol  │
│ AACG  —  08-19   │ AMZN  History says a $10,000 investment…    │ filing  │
│ AAPL  1  —       │ MSFT  How IREN is cashing in on AI…         │         │
├──────────────────┴─────────────────────────────────────────────┤ …ask a  │
│ REPORTING SOON                                                 │ question│
└────────────────────────────────────────────────────────────────┴─────────┘
```

## What makes it different

**The AI cannot make a claim it can't back.** Every answer is checked against
records the server itself fetched, and anything unverifiable is *deleted before
you see it* — three mechanisms, one per surface:

| Surface | The model cites | The server verifies against |
|---|---|---|
| Window ask | item index | the numbered window it was handed |
| Filing Q&A | a verbatim quote | a substring check on the real SEC text |
| Symbol research | section index | the sections this server actually fetched |

**It admits when its data is too thin.** Free market-data feeds carry a
fraction of consolidated volume, so an illiquid name's "1-minute" chart can be
a handful of prints stretched across days — and it renders *identically* to a
real one. AlphaDesk measures bar coverage and gap size and **hides** RSI/MACD
below the floor rather than drawing something that looks trustworthy and isn't.

**The window is not ranked.** Every symbol with fresh news or an upcoming
report, alphabetically. Sort a column if you want; nothing is ordered for you
by an opinion you can't inspect.

## Quick start

```bash
pip install -r requirements.txt
cp alphadesk/deploy/env.example .env      # then edit it
python -m alphadesk.main dashboard        # http://127.0.0.1:8000
```

Frontend development:

```bash
cd alphadesk/ui && pnpm install && pnpm dev     # proxies /api to :8000
```

### Minimum configuration

```ini
ALPACA_API_KEY=...            # market data + the tradable universe
ALPACA_SECRET_KEY=...
LLM_API_KEY=...               # any OpenAI-compatible endpoint
SEC_USER_AGENT=YourApp (you@example.com)   # SEC requires real contact info
```

Everything else has a working default. SEC EDGAR needs no key at all.

## Pluggable

Three seams, each a `Protocol` with a name you select in config:

```ini
LLM_PROVIDER=openai-compatible   # deepseek · openai · groq · ollama · lmstudio
# LLM_PROVIDER=anthropic
NEWS_PROVIDER=polygon            # or alpaca
PRICE_PROVIDER=builtin           # alpaca + yfinance
```

Bring your own without forking — a local module via `ALPHADESK_PLUGINS`, or a
published package via the `alphadesk.providers` entry point. Registering an
existing name replaces the built-in. See **[docs/providers.md](docs/providers.md)**.

Dashboard tiles work the same way: a widget registers itself
(`ui/src/widgets/registry.ts`), and the page renders whatever is registered.

## Agent access (MCP)

The same data the UI reads is exposed to agents over
[MCP](https://modelcontextprotocol.io):

```bash
python -m alphadesk.main mcp           # stdio
python -m alphadesk.main mcp --http    # streamable HTTP
```

```json
{"mcpServers": {"alphadesk": {"command": "python", "args": ["-m", "alphadesk.main", "mcp"]}}}
```

Eleven read-only tools: `market_tape`, `quote`, `movers`, `price_chart`,
`screener_window`, `screener_ask`, `list_filings`, `filing_ask`,
`research_ask`, `earnings_calendar`, `recently_reported`.

The three `*_ask` tools return their **citations**, so an agent calling them
inherits the verification instead of having to reproduce it — a claim whose
source could not be checked was already dropped before the tool returned.

## Run the model locally

Nothing here requires a hosted LLM. Point it at Ollama and no key is needed:

```ini
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3.1
```

## Layout

```
alphadesk/
  providers/    the plugin seams: LLM, news, prices + the registry
  ingest/       news polling, SEC EDGAR, earnings calendar, price/indicator math
  desk/         the three AI surfaces: window ask, filing Q&A, symbol research
  ai/           one transport: text in, JSON out, injection-guarded, cost-tracked
  ledger/       SQLite store — articles, filings, caches, token spend
  app/          FastAPI + the built SPA
  ui/           React 19 + Vite; dense terminal styling, no component library
```

## Not a trading system

Earlier versions of this repo traded. Two autonomous engines were built,
measured against the S&P, and deleted: **−0.072%** mean alpha over 503
backtested trades, **−1.123%** over 44 live ones. The manual booking layer that
replaced them was removed too, on 2026-08-18, when the project became a
consumption product.

There is no order routing, no position state and no broker integration in this
codebase. If you want that, the history is in git — but the measured result is
in the numbers above.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). New data sources should be providers,
not edits to `ingest/`.

## Data sources and terms

**[docs/data-sources.md](docs/data-sources.md)** lists every upstream, how it is
collected, and what its terms are. Read it before running a public instance —
two sources are unofficial endpoints rather than licensed APIs, and one
dependency's licence conflicts with the one declared below.

## Licence

MIT — **but see [docs/data-sources.md](docs/data-sources.md)**: `openbb-core`
and `openbb-sec` are AGPL-3.0-only, which is not consistent with an MIT
declaration for the combined work. That is unresolved.
