# CLAUDE.md

Guidance for AI coding agents working in this repo.

**AlphaDesk is a CONSUMPTION terminal**: it fetches, reads and presents market
information. It does not trade, hold positions, route orders or score
decisions. It is **open source and pluggable** — the LLM, the news feed and
market data are all swappable plugins, and so are dashboard tiles.

Read this before changing anything, because several obvious-looking
"improvements" here are things that were already tried and deliberately undone.

## History you will otherwise re-litigate

Three deletions, each measured rather than guessed. Don't reintroduce them
without new evidence:

1. **The autonomous trading engines (2026-08-16).** Two of them, both measured
   against SPY: **−0.072%** mean alpha over 503 backtested trades, **−1.123%**
   over 44 live exits at a 38.6% win rate.
2. **The manual trading layer (2026-08-18).** Booking, tiered exits, forward
   grading, human-vs-machine scoring — ~6,400 lines. Removed when the product
   became consumption rather than measurement. `quant/`, `ledger/grader.py`,
   `desk/plan.py`, `desk/portfolio.py` are all in git history.
3. **Screener ranking (2026-08-18).** Code-computed scoring plus an auto-digest
   of the top N. Ordering a list is a judgment; the reader makes it.

Also reversed once, in the other direction: `desk/research.py` shipped as a
tool-calling agent (`1314ec4`) and was rewritten to server-side pre-fetch one
commit later (`59b210c`). Don't add a tool loop back without a workload that
needs one.

## The invariants

These are the product. Breaking one is a bug even if tests pass.

1. **No claim renders without a verified source.** Three mechanisms:
   `desk/screener.py` resolves ITEM INDEX into the numbered window it built;
   `desk/filings.py` checks each quote is a real substring of the cached SEC
   text; `desk/research.py` resolves SECTION INDEX into sections the server
   fetched. All three **drop** what doesn't verify. Never render an
   unverifiable claim with a caveat instead. Covered by `tests/test_attribution.py`.
2. **Indicators hide themselves when the feed can't support them.**
   `_coverage_stats()` measures bar count and median gap; below
   `CHART_MIN_COVERAGE` / `CHART_MAX_MEDIAN_GAP_MIN` the UI hides RSI/MACD.
   Measured: ENTA had 92 bars across 5 sessions with a 42-minute p90 gap
   against AAPL's 1570 at 1.0 — and the two charts render identically. Do not
   "fix" this by drawing anyway.
3. **The window is not ranked.** `inventory()` is a pure DB read, alphabetical.
   Sorting a column in the UI is the reader choosing; a default order is the
   app deciding. Alphabetical is also stable, so rows don't reshuffle under the
   cursor on the poll.
4. **The AI speaks only when asked.** No background digests. An idle terminal
   spends nothing, and `/api/screener` needs no LLM at all — an outage leaves
   the window and its real headlines intact and fails only the ask (422).
5. **Untrusted text stays untrusted.** `wrap_data()` delimits external content
   and neutralises nested delimiters; the injection guard is appended to every
   system prompt. News is attacker-reachable in principle — a press release can
   contain text aimed at the summarizer.

## Architecture

```
providers/     THE PLUGIN SEAMS. base.py has the Protocols (LLMProvider,
               NewsProvider, PriceProvider); registry.py handles registration,
               entry-point discovery and env selection. llm.py ships
               openai-compatible + anthropic; news.py ships polygon + alpaca;
               prices.py wraps the builtin Alpaca+yfinance implementation.
ai/llm.py      one call shape: text in, JSON out. Injection guard, input-size
               cap, token accounting per role. Delegates to the selected
               provider — no vendor here.
ingest/news.py poll the news provider -> persist -> enrich (category/sentiment)
ingest/edgar.py SEC EDGAR: ticker->CIK, filing list, text extraction. Free, no
               key. Needs SEC_USER_AGENT — see its docstring for two things
               that silently break (the UA requirement, and iXBRL).
ingest/earnings.py Nasdaq calendar -> the earnings window
ingest/prices.py Alpaca live + yfinance; chart series with RSI-9/MACD,
               _coverage_stats, fundamentals, ownership, macro, sector, movers
ingest/insider.py SEC Form 4 insider trades, parsed from EDGAR XML directly.
               NOTE: primaryDocument points at the XSL-RENDERED view; the raw
               XML is the bare filename in the same folder. Derivative rows
               (options/RSUs) are excluded — only share trades answer "did an
               insider buy". 13F deliberately NOT here: it's filed BY a
               manager, so it can't answer "who holds this stock".
desk/screener.py unranked window (pure DB read) + ask() over the whole window
desk/filings.py  Q&A over ONE filing, verbatim quotes verified server-side
desk/research.py Q&A over ONE symbol from 6 pre-fetched sections
ledger/store.py  SQLite/WAL: news_articles, enrichment_cache, symbol_digests,
               filings + text + qa caches, research_cache, earnings,
               token_usage. NOTE: init() DROPS the retired trading tables
               (picks/runs/funnel/skips/...) on every start — destructive and
               deliberate (816314b). An old ledger loses that history the first
               time it boots this version; back it up first if it matters.
app/dashboard.py FastAPI: screener(+ask), filings(+ask), research/ask, chart,
               quote, movers, tape, earnings, tokens, system, + the SPA
mcp_server.py  the same data as 11 read-only MCP tools. The *_ask tools return
               their citations, so an agent inherits the verification rather
               than reproducing it. Read-only by construction — there is no
               write surface to expose.
net.py         socket deadlines for alpaca-py, which ships without any. Every
               endpoint here is a sync def on a 40-worker threadpool, so an
               unbounded upstream parks workers rather than failing.
main.py        two ingest loops (news, earnings) + the web server
ui/            React 19 + Vite. Dense terminal styling, NO component library —
               hand-rolled primitives in components/terminal.tsx. Dashboard
               tiles come from widgets/registry.ts, not hardcoded JSX.
               The chart is OURS: components/chart/ChartCanvas.tsx renders SVG
               against lib/chartScales.ts. lightweight-charts was removed —
               the reason was control, not appearance. Candles are batched
               into four paths, so node count is constant in bar count; do not
               "simplify" that to one element per bar.
```

## Commands

```bash
pip install -r requirements.txt
python -m alphadesk.main dashboard      # API + SPA on :8000
python -m alphadesk.main earnings       # refresh the calendar, print it
python -m alphadesk.main mcp            # serve the same data to agents
python -m pytest -q                     # 66 tests

cd alphadesk/ui && pnpm dev             # frontend HMR, proxies /api
cd alphadesk/ui && pnpm build           # tsc -b + vite build -> app/static/
```

**`npx tsc --noEmit` is a no-op here** — the root tsconfig is `files: []` with
project references, so it exits 0 on code that cannot compile. Use `tsc -b`.

## Frontend conventions

- **13px root font.** Every rem-based size scales off it; that one line is what
  makes the terminal dense.
- **No component library.** shadcn/ui and its dependencies were removed
  (bundle 326kB → 245kB). Don't reintroduce one.
- **Shared query keys** (`lib/queries.ts`). Two components asking for the same
  endpoint share one request — a hand-rolled `setInterval` re-fetches it
  separately. `refetchIntervalInBackground` is set on purpose: this is a
  terminal that lives on a second monitor, and Query pauses hidden-tab polling
  by default.
- **Widgets register themselves.** Add a tile in `widgets/`, don't edit
  `DashboardPage`.
- **Without `tailwind-merge`, a caller's `w-24` does not beat a base `w-full`.**
  Shared class constants must not ship widths.

## Licensing

MIT, and every dependency is permissive. `openbb-core` / `openbb-sec` were
removed on 2026-08-18 — they are AGPL-3.0-only, which does not combine with an
MIT declaration for a network-served app. They backed one feature, Form 4
insider trades; `ingest/insider.py` now reads that from EDGAR directly. Don't
reintroduce a copyleft dependency without deciding the project's licence first.

Two upstreams are unofficial endpoints rather than licensed APIs (`yfinance`
scrapes Yahoo; the earnings calendar reads an undocumented `api.nasdaq.com`
route). Documented in `docs/data-sources.md`; they matter for a public
instance.

## Known gaps

- **No frontend tests.** Python is covered (66 tests); the UI has none.
- **`@tanstack/react-virtual`'s `measureElement` did not fire** for expanding
  rows in the earnings table (verified at 289-row scale). Sizes there are
  computed instead, with the expanded panel a fixed height. If you fix the
  underlying cause, the fixed height can go.
- **`react-table` is pinned to v8.** v9 is a ground-up rewrite nothing here
  needs.
