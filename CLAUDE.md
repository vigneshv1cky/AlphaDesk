# CLAUDE.md

Guidance for AI coding agents working in this repo. **This describes a HUMAN-
OPERATED terminal.** Two architectures were removed before it: the LLM
multi-agent system (`11263ae`, 2026-08-07) and every trading bot
(2026-08-16). There are ZERO autonomous trades. There is exactly ONE LLM
TRANSPORT (`ai/deepseek.py::chat_json`, added 2026-08-16 evening) and four
read-only callers on top of it — news enrichment, screener asks, filing Q&A,
and symbol research. Every one of them reads and summarizes for a human; none
decides, sizes, or books anything. Only enrichment runs unattended; the other
three fire when a person asks a question.

## What this is

**AlphaDesk** — a research and execution terminal for ONE human trader. **There
are no trading bots.** Every autonomous decision path was deleted on
2026-08-16 (see below); a trade enters this system exactly one way, through
`POST /api/picks/manual` when a person clicks Book.

What the machine still does is the work a person can't: watch data
continuously, read more news than a human has time for, manage exits on
positions you opened, and keep an honest score.

- **The Screener shows you the window — it does NOT rank it.** `/screener`
  (the default landing page) lists every symbol with fresh news or a report
  inside `SCREENER_HORIZON_DAYS`, alphabetically, with its raw headlines. No
  score, no top-N, no digests written in the background. Ask a question and
  ONE call reads the whole window — every article and report, across every
  symbol — and answers it with cited sources.
- **Then you dig in.** `/filings` asks a question of ONE SEC filing;
  `/research` asks a question about a symbol's fundamentals, ownership,
  insider trades, earnings history, macro, and sector. Both answer only from
  data the server fetched, and both drop any claim whose citation doesn't
  check out.
- **You decide.** Click "Trade →" on a Screener row (or type a symbol
  directly) to land on `/trade`: candles + RSI-9 + MACD(12,26,9), a
  data-quality verdict, and a booking form that demands a written thesis.
- **The machine executes and measures.** `quant/watcher.py` holds the
  target/stop/trail/session-close on whatever you booked;
  `ledger/grader.py` scores it forward vs SPY, tracks MFE/MAE, and charges
  borrow on shorts.
- **Research / paper only — no real money.** Alpaca PAPER order routing for
  ENTRIES was removed with the bots; only the closing path remains.

### Why the bots are gone (2026-08-16)

The autonomous engine was measured, not abandoned on a hunch:

- **−0.072% mean alpha** over 503 backtested trades (`ledger/rsi_backtest.py`,
  90 days, real 1-min bars), with **87% of the loss** in positions that hadn't
  reverted before the forced 15:45 exit.
- **−1.123% mean alpha** over 44 live exits, 38.6% win rate.
- The predecessor drift engine was equally flat across 1700+ backtested
  reports and 300+ live exits.

Two structural findings from that work still shape the terminal:

- **The IEX feed is too thin for indicators on illiquid names.** ENTA: 92 bars
  across 5 sessions, 42-min p90 gap, 16% of consecutive bars actually 1 minute
  apart — against AAPL's 1570 bars at a 1.0-min median. A sparse "1-minute"
  MACD renders *identically* to a real one, so the UI suppresses indicators
  below `CHART_MIN_COVERAGE` / `CHART_MAX_MEDIAN_GAP_MIN` instead of drawing a
  chart that would mislead its own operator.
- **`plan.atr_plan()` was dead code the whole time.** `PLAN_TARGET_ATR` 2.0 vs
  `MA_STOP_BACKSTOP_ATR` 4.0 is a reward/risk of 0.5, and `_coherent()`
  enforces `MIN_RISK_REWARD_RATIO` 1.5 — so it returned `None` on every call
  and the engine silently used a fallback branch that skips the min-stop
  clamps. Still unfixed; the manual endpoint hits the same path.

## Commands

```bash
pip install -r requirements.txt

python -m alphadesk.main dashboard      # FastAPI + SPA at :8000 — the terminal
python -m alphadesk.main grade          # grade due picks / update MFE-MAE
python -m alphadesk.main status         # ledger summary
python -m alphadesk.main backtest       # does drift pay on history? (--selection for the score test)
python -m alphadesk.main abtest         # reaction-gate A/B (forward alpha by reaction size)
python -m alphadesk.main alpha          # alpha_net vs beta-adjusted alpha_adj
python -m alphadesk.main earnings       # refresh calendar, show upcoming/recent

cd alphadesk/ui && pnpm build           # rebuild SPA → app/static/
```

## Trading model (the core design)

- **Session-scoped trades.** Each market window is its own trade: PRE 4:00–9:30,
  OPEN 9:30–16:00, AFTER 16:00–20:00. A pick entered in a session exits at that
  session's close — **nothing carries into another market**. Night (20:00–4:00)
  is not tradeable; a pick decided at night is stamped PRE and enters at the next
  4:00 open.
- **Per-market buffers** (`config.py`): START_BUFFER_MIN=15 (skip the volatile
  open), ENTRY_BUFFER_MIN=60 (no new entries in the final hour — never buy when
  we're about to close), EXIT_BUFFER_MIN=15 (positions close before the boundary).
  So entries: PRE 4:15–8:30, OPEN 9:45–15:00, AFTER 16:15–19:00; exits: 9:15 /
  15:45 / 19:45 — the last entry still gets ~45 min before the exit. (In
  practice only the OPEN window is used — the entry engine is OPEN-only; the
  PRE/AFTER math still governs exits for anything already open.)
- **Entry** is a human clicking Book on `/trade`. It fills at the live price;
  the endpoint refuses if the last print is staler than `MANUAL_MAX_QUOTE_AGE_S`
  (15 min), which also catches a halted symbol. A written `thesis` is required —
  a decision with no recorded reason can't be learned from.
- **Exit** is pure code and fully automated: hard target/stop, trailing stop,
  give-back, spike reversal, stale expiry, RSI signal-reversal, and the
  session-close sweep — all in `quant/watcher.py`, which picks up positions via
  `open_taken_picks()` regardless of who booked them. `record_exit` stamps
  `alpha_net = exit alpha` + `graded_at` (an exit IS the grade).
- **Risk rails.** `DAILY_LOSS_STOP_PCT` and the per-symbol caps governed the
  deleted bot; they are deliberately NOT enforced against a human decision.
  `entry_allowed()` survives as a non-blocking *warning* on the booking
  response when you're inside the session's entry buffer.

## The terminal (`/trade`)

- **Charts.** `GET /api/chart/{symbol}` → OHLC + full RSI-9 and MACD(12,26,9)
  series, rendered by `ui/src/components/PriceChart.tsx` (lightweight-charts) in
  three time-synced panes.
- **MACD is DISPLAY ONLY.** It was removed from the bot because two automated
  signals can disagree with nothing to arbitrate. That was a *machine* problem:
  a human reading a chart resolves "RSI oversold but MACD hasn't turned" with
  judgment. Never wire it back into an automated decision.
- **Data-quality gating is load-bearing, not cosmetic.** `_coverage_stats()`
  reports bar count, sessions, coverage vs a 390-bar session, and median gap.
  Below the floors the UI **hides** RSI/MACD and shows why. Do not "fix" this by
  drawing them anyway — see the ENTA numbers above.
- **Booking.** `POST /api/picks/manual` → `trigger_src="HUMAN"`. Exits and
  grading attach automatically; no extra wiring.
- **Scoring.** `/api/performance` splits `by_decider` (HUMAN vs MACHINE) so
  human decisions are scored on identical terms to the bot's historical rows.

## The AI research layer — one transport, four read-only callers

`ai/deepseek.py::chat_json` — plain `deepseek-chat` via `/chat/completions`
(OpenAI-compatible), JSON mode. No multi-provider abstraction, no rate-limit
ladder, no tool-use loop: those existed in the deleted v1 system because a
COMMITTEE decided trades and needed to survive provider outages. This
summarizes text for a human; on failure the caller drops that item and logs
why (`DeepSeekError`) — there is nothing to protect with a retry ladder.

The four callers, each with its own `role` label so `/api/tokens` attributes
cost per feature: `news-enrich` (`ingest/news.py`), `screener-ask`
(`desk/screener.py`), `filing-qa` (`desk/filings.py`), `research-agent`
(`desk/research.py`). `news-enrich` is the only one that runs on a timer.

- **Hard rule, not a phase: no claim renders without a source.** Each caller
  enforces it in the form its data allows, and all three checks run
  SERVER-SIDE against records we control — the model's own assertion about a
  source is never trusted:
  - **screener** — cites by ITEM INDEX into the numbered window it was shown
    (articles AND earnings rows share one index space), resolved back to the
    stored article or calendar row (`_resolve_citations`).
  - **filings** — no numbered list to cite, so the model must quote VERBATIM
    and every quote is checked as a real substring of the cached SEC text
    (`_verify_quotes`); a quote that doesn't verify is dropped, not shown.
  - **research** — cites by SECTION INDEX into the sections this server
    fetched (`_resolve_citations`); a citation pointing at a section that
    came back unavailable is dropped.
- **Injection defense.** `wrap_data()` delimits untrusted article text in
  `<data:*>` blocks; the system prompt is told those blocks are never
  instructions. News text is attacker-reachable in principle (a press
  release could contain text aimed at the summarizer) — this is why.
- **The screener ranks NOTHING, and the AI speaks only when asked.**
  `inventory()` is a pure database read: every symbol in the window,
  alphabetical, no score. `ask()` is the only place that page spends tokens.
  This replaced a two-stage design (code-computed ranking by earnings
  proximity + news volume, AI auto-narrating the top `SCREENER_TOP_N`) on
  2026-08-18 — ordering a list IS a judgment, and this terminal's premise is
  that the judgment belongs to the operator. Two consequences worth keeping:
  - **Alphabetical is load-bearing, not laziness.** Don't "improve" it by
    sorting on volume, recency, or move size — that's the ranking coming
    back in through the sort key. It's also stable, so rows don't reshuffle
    under the cursor on the 60s poll.
  - **An idle terminal costs nothing.** No background digests means no spend
    until someone asks, and `/api/screener` needs no LLM at all — a DeepSeek
    outage leaves the full list and its real headlines intact and turns only
    the ask into a clean 422, never an empty page or a 500 (verified with a
    simulated outage).
- **Pipeline:** `ingest/news.py` polls Polygon (`POLYGON_API_KEY`,
  `list_ticker_news`) → persists raw articles to `news_articles` → enriches
  (category/sentiment/relations) into the pre-existing `enrichment_cache`
  table, unchanged from v1. `main.py`'s `_news_loop` runs exactly that much
  on `NEWS_REFRESH_MINUTES` — ingest and enrichment, nothing else. It no
  longer calls into `desk/screener.py`: there is no digest cache to pre-warm,
  because `/api/screener` is a plain database read. Screener answers are
  cached in `symbol_digests` under the sentinel symbol `*SCREENER-ASK*`,
  keyed on a hash of the question PLUS the exact item set behind it, so
  re-asking while no new news has landed is free.
- **Research is pre-fetch, NOT a tool-calling loop — and that was a
  deliberate reversal, one commit later.** `desk/research.py` first shipped
  as an autonomous agent choosing its own tools turn-by-turn (`1314ec4`,
  the roadmap's "agentic research layer"); `59b210c` rewrote it so the
  SERVER pre-fetches all six sections and ONE `chat_json()` call synthesizes
  from exactly that — same shape as `desk/filings.py`. The recorded reason
  is simply that it's simpler and the better fit for this workload; the
  structural consequences are what matter downstream:
  - `ai/deepseek.py`'s `run_tool_loop` / `_PROVIDE_ANSWER_TOOL` were deleted
    outright. `chat_json` is again the only thing in that module.
  - `ask()` takes `(symbol, question)`, not a symbol-free question — the
    SERVER decides what to fetch, so the model never has to infer the
    symbol. `/research` has a symbol field, matching Filings and Trade.
  - Citations moved from "a real, server-captured tool call" to SECTION
    INDEX against the pre-fetched sections. Those six sections ARE the
    citable universe and are returned to the UI as the "Data used" trail.
  - `research_cache`'s primary key changed shape (`question_hash` alone →
    `(symbol, question_hash)`). SQLite can't ALTER a PK, so `store.init()`
    carries a one-time drop-and-recreate — correct here precisely because
    it's purely a cache.
  Don't reintroduce a tool loop here without a workload that actually needs
  one.
- **Token spend is honest.** Every call runs through `store.record_tokens()`
  into the pre-existing `token_usage` table, visible at `/api/tokens`.
- **Recovered, not reinvented.** The Polygon fetch, the enrichment prompt,
  and `enrichment_cache`/`token_usage` are the SAME code/schema as v1 (git
  `11263ae~1`) — only the LLM transport changed (the old multi-role
  `call_role()` → this repo's single-purpose `chat_json()`).

## Architecture

```
ai/deepseek.py       the ONE LLM transport — chat_json(): plain deepseek-chat,
                     JSON mode, injection guard, token accounting. Four
                     read-only callers. No decisions here.
ingest/earnings.py   Nasdaq calendar → watchlist, UNFILTERED (-3 to +5 days
                     around the report). A human reading the terminal judges.
ingest/news.py       Polygon ticker news poll → enrich (DeepSeek) → persist
                     (news_articles + enrichment_cache)
ingest/edgar.py      SEC EDGAR — free, no key. Ticker→CIK, filing list,
                     document fetch + BeautifulSoup text extraction, full-text
                     search. See its module docstring for 3 easy-to-get-wrong
                     facts (User-Agent requirement, ciks= not tickers=, iXBRL).
ingest/openbb_ownership.py
                     SEC Form 4 insider trades — free, keyless, via
                     openbb-sec's Fetcher class DIRECTLY (no obb router, so
                     none of the ~50 other provider packages). Returns None on
                     any failure. Institutional ownership is deliberately NOT
                     here: Form 13F is filed BY a manager, so it can't answer
                     "who holds this stock" — that stays on yfinance
                     (prices.get_institutional_ownership). Verified live
                     before choosing this; see its module docstring.
ingest/prices.py     price context (Alpaca live + yfinance), intraday RSI-9 +
                     MACD + _coverage_stats(), get_chart_series(), options IV,
                     fundamentals, institutional ownership, sector ETFs, macro
quant/signals.py     6 weighted signals → composite (OFFLINE ONLY: backtest,
                     grader, dashboard display — nothing trades on it)
quant/calibrate.py   online + batch weight learning from graded outcomes
quant/watcher.py     tiered exits (TP/trail/give-back/stop/spike/stale/
                     RSI-reversal/session-close) — manages YOUR positions
quant/stream.py      Alpaca WebSocket live prices (SPY registered)
desk/screener.py     UNRANKED inventory of the window (pure DB read, no LLM,
                     alphabetical) + ask(): ONE call over every article and
                     upcoming report at once, cited by item index, cached in
                     symbol_digests under '*SCREENER-ASK*'. The front door.
desk/filings.py      Q&A over ONE filing, answers backed ONLY by verbatim
                     quotes verified as real substrings of the SEC document
                     text server-side — stronger attribution than the
                     screener's index-citations, since there's no numbered
                     list to cite, just one long document
desk/research.py     Q&A over ONE symbol from 6 server-fetched sections
                     (fundamentals / institutional ownership / insider trades
                     / earnings history / macro / sector), cited by SECTION
                     INDEX, cached in research_cache with a real TTL (the DATA
                     goes stale even when the question doesn't). Pre-fetch by
                     design, not a tool loop — see the AI-layer section.
desk/plan.py         ATR-based entry/target/stop + level-crossing resolution
desk/portfolio.py    position CLOSING + reconcile (entry routing deleted)
ledger/store.py      SQLite/WAL ledger + funnel/skips + price_daily +
                     news_articles + symbol_digests + enrichment_cache +
                     filings + filing_text_cache + filing_qa_cache +
                     research_cache + token_usage
ledger/grader.py     forward grading vs SPY (alpha_net/alpha_adj), MFE/MAE
ledger/backtest.py   daily-bar drift research (uses the local price cache)
ledger/rsi_backtest.py  intraday replay of the RETIRED entry engine
app/dashboard.py     FastAPI: /api/* incl. /api/screener (DB read, no LLM) +
                     /api/screener/ask + /api/chart + /api/filings/{symbol} +
                     /api/filings/ask + /api/research/ask +
                     /api/picks/manual + SPA
app/alerts.py        webhook notifications (ALERTS_WEBHOOK_URL)
main.py              CLI + 6 async loops (grader, earnings+arm, NEWS ingest
                     +enrich only, position watch, quant watch, daily
                     summary) — NO entry loop, and nothing pre-narrates
ui/                  React 19 + TS + Vite → app/static/. Nav is two-tier: the
                     product loop (/screener /filings /research /trade
                     /performance) foregrounded, back-office (/live /history
                     /earnings /system) demoted. / redirects to /screener; /open
                     redirects to /live (MarketPage.tsx deleted 2026-08-17 —
                     it was a near-duplicate of Live+History filtered to one
                     session, a leftover of the old multi-session bot loop;
                     both pages now carry an inline session filter instead).
                     Screener rows link to both /filings?symbol= and
                     /trade?symbol=.
```

**Deleted with the bots (2026-08-16)** — recover from git if ever needed:
`desk/watcher.py` (entry engine), `desk/workflow.py` (`research_run` batch
booking), `main.py::_entry_watch_loop`, the `desk` CLI subcommand,
`portfolio.route_pick` (autonomous broker entries), `tests/test_watcher.py`,
`AutoRunStatus.tsx`. `desk/stream.py` went earlier (2026-08-13).

Historical ledger rows keep their old `trigger_src` (`ENTRY_WATCH`,
`FIND_TRADES`) and are what the MACHINE arm on `/api/performance` reports.
Nothing new is written under them.

## Environment variables (key)

```ini
ALPACA_API_KEY / ALPACA_SECRET_KEY   # market data + universe
POLYGON_API_KEY                      # ingest/news.py's article source
DEEPSEEK_API_KEY                     # ai/deepseek.py — the one LLM transport
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat         # not deepseek-reasoner: summarization, not multi-step reasoning
LLM_MAX_INPUT_CHARS=24000            # global default, sized for batched headlines
NEWS_REFRESH_MINUTES=20 / NEWS_LOOKBACK_HOURS=36
SCREENER_HORIZON_DAYS=5              # upcoming-earnings window for the inventory
SCREENER_ASK_MAX_ARTICLES=120        # /api/screener/ask input cap — oldest dropped first
SCREENER_ASK_MAX_CHARS=40000         # ...and its per-call char budget (the ask is deliberately wide)
FILING_MAX_CHARS=60000 / FILING_RECENT_LIMIT=12   # ingest/edgar.py — no API key needed, SEC is free
RESEARCH_MAX_CHARS=30000             # desk/research.py per-call input budget (6 JSON sections)
RESEARCH_CACHE_TTL_HOURS=4           # real TTL: the DATA ages, not just the question
OWNERSHIP_TTL_S=21600                # 13F quarterly / Form 4 event-driven — both move slowly
ALPHADESK_DATA=~/.alphadesk          # ledger.db, universe.json, quant_weights.json

# terminal behaviour (the live path)
CHART_MIN_COVERAGE=0.5               # below this, indicators are HIDDEN
CHART_MAX_MEDIAN_GAP_MIN=2.0         # ...and below this gap quality too
MANUAL_MAX_QUOTE_AGE_S=900           # refuse to book on a stale print
START_BUFFER_MIN=15 / ENTRY_BUFFER_MIN=60 / EXIT_BUFFER_MIN=15
WATCH_INTERVAL_S=60 / QUANT_TIERED_EXITS=1 / QUANT_STREAM_ENABLED=1
ALERTS_WEBHOOK_URL=                  # Telegram/Slack/Discord webhook
SHORT_BORROW_APR / SHORT_BORROW_APR_ILLIQUID   # honest-alpha borrow charge

# RETIRED ENGINE — read only by ledger/rsi_backtest.py. Changing these changes
# a research replay, NOT any live behaviour.
RSI_CROSS_OVERSOLD=30 / RSI_CROSS_OVERBOUGHT=70
MA_ENTRY_MIN_RVOL=1.2 / MA_ENTRY_MIN_ATR_PCT=1.5
MA_INTRADAY_HISTORY_DAYS=5 / MA_STOP_BACKSTOP_ATR=4.0
MAX_BOOKINGS_PER_SYMBOL_PER_DAY=2
MATERIAL_REACTION_PCT=1.5            # offline abtest/backtest only
```

## Deployment (manual, no CI)

GCP VM `alphadesk` (34.182.195.6:8000), project `alphadesk-research`,
account `vignesh90085@gmail.com`. Data + `.env` at `/opt/alphadesk-data` and
`/opt/alphadesk/.env`; repo at `/opt/alphadesk`. VM is UTC; all session clocks are ET.

```bash
# commit + push FIRST, then:
gcloud compute scp --zone=us-east4-a <file> alphadesk:/tmp/<file>
gcloud compute ssh alphadesk --zone=us-east4-a \
  --command="sudo cp /tmp/<file> /opt/alphadesk/alphadesk/<path> && sudo systemctl restart alphadesk"
# static: sudo rm -rf the old static dir, then cp -r the new one (stale bundles)
```

## Honest status (2026-08-17)

- **Nothing trades itself.** The terminal is the product; the operator is the
  strategy. There is no validated edge in this repo — two autonomous attempts
  were measured and both were flat-to-negative (numbers at the top).
- **Phase 0 shipped:** charts, indicators, data-quality gating, manual booking,
  automated exits on human positions, human-vs-machine scoring.
- **AI research layer shipped:** Screener (code-ranked + AI-digested news +
  earnings) is now the default landing page, wired to `/trade` via
  `?symbol=`. Recovered Polygon news + enrichment from the deleted v1 system
  rather than rebuilding; DeepSeek transport is new and purpose-built.
- **`DEEPSEEK_API_KEY` rotated and confirmed working**, both locally and on
  the VM (`/opt/alphadesk/.env` updated 2026-08-17 via the deploy that shipped
  the frontend rewrite) — the original key was rejected (`HTTP 401`), the
  fresh one authenticates and produces real cited digests end-to-end in
  production.
- **Phase 1 shipped (2026-08-17): the filings workspace.** `/filings` —
  free SEC EDGAR, no vendor, no API key. Pick a symbol, browse its recent
  10-K/10-Q/8-K filings, ask one a question; every answer is backed ONLY by
  quotes verified as real substrings of the actual SEC document text
  (`desk/filings._verify_quotes`) — a stronger guarantee than the screener's
  index-based citations, since a filing is one long document, not a numbered
  list. Verified end-to-end against real filings (AAPL, EHC) before shipping.
  NOT built yet: cross-document Q&A (multiple filings at once), peer-
  comparison matrices, user-uploaded document contrast — the original DSX-
  analog scope, left as later increments on top of this ingestion layer.
- **Phase 2 shipped (2026-08-17): the research workspace.** `/research` —
  ask a question about one symbol, answered from six server-fetched sections
  and cited by section index, with the "Data used" trail rendered under the
  answer. Insider trades come from SEC Form 4 via `openbb-sec`'s Fetcher
  class directly (free, keyless); institutional ownership stays on yfinance
  for the reason in `ingest/openbb_ownership.py`'s docstring. Built first as
  a tool-calling agent, then deliberately rewritten to pre-fetch — see the
  AI-layer section for why, and don't undo it casually.
- **Screener ranking removed (2026-08-18).** The two-stage design (code
  ranking + auto-digest for the top N) is gone: the page is now an unranked
  alphabetical inventory plus `POST /api/screener/ask`, one call over the
  whole window. `SCREENER_TOP_N` was deleted with it. Verified end-to-end —
  unranked output, out-of-range citations dropped, cache hit on a repeat
  ask, and a DeepSeek outage returning 422 on the ask while `/api/screener`
  still serves the full list.
- **Planned next** (not built): news theme grouping; cross-document filing
  Q&A, peer-comparison matrices, and user-uploaded document contrast (the
  rest of the Phase 1 scope). Attribution (no claim without a source) is
  enforced everywhere built so far, not deferred to a later phase.
- **Known bug, unfixed:** `plan.atr_plan()` returns `None` for every call under
  the current multipliers (see top). The manual endpoint falls through to the
  same fallback the bot used.
- **Open question the ledger will answer:** whether a human's judgment beats
  the −1.123% mean alpha the machine posted. The HUMAN arm has no closed trades
  yet.

## Drift-engine findings (2026-08-07, superseded — kept for history)

- **The post-earnings-drift edge is NOT validated.** Backtests (1700+ reports,
  raw + composite-selection) and 300+ live exits all land near-zero-to-negative.
  The material-reaction gate doesn't select winners (dropped arm ≈ better).
- **Where a pocket might be:** composite score **5–10**, **OPEN** session,
  **LONG** only (+3% α in the selection backtest). High scores (>20) and PRE/AFTER
  sessions and SHORTs lose consistently. Tuning toward that pocket is the live
  decision on the table — not yet applied; the user is running all sessions to
  build the sample first.
- Live per-market P&L at the time (Performance page): OPEN profitable, PRE/AFTER
  heavy drags — one input into making the current engine OPEN-only. (The
  position/concentration rails described here were since removed; see the
  Trading model section for what's actually enforced now.)
- Backtests read a **local daily-price cache** (`price_daily` table) — backfill
  once via `--days N` / first run, then re-runs are ~1 min, not rate-limited.
- Previous session notes (2026-07-27) about the LLM system are obsolete.
