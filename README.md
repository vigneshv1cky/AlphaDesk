# AlphaDesk

A pure-quant **post-earnings-drift research engine**. No LLM, no agents — a weighted
set of statistical signals scores each earnings reporter, the desk books
session-scoped paper positions, and a self-grading ledger scores every call forward
vs SPY.

**Research / paper only — no live order execution.** (Alpaca PAPER fills are opt-in
and off by default.)

## Table of contents

- [The idea](#the-idea)
- [How a run works](#how-a-run-works)
- [The signals](#the-signals)
- [Trading model](#trading-model)
- [Risk rails](#risk-rails)
- [Ledger and grading](#ledger-and-grading)
- [Backtesting](#backtesting)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Configuration](#configuration)
- [Repository layout](#repository-layout)
- [Design principles](#design-principles)
- [Status](#status)
- [Disclaimer](#disclaimer)

## The idea

Markets price a headline in seconds, but the *consequences* take days to propagate.
Post-earnings drift (PEAD) is the classic example: after a big earnings reaction,
the move often keeps going as algos price the headline but miss the nuance. The
desk watches the Nasdaq earnings calendar, waits for a material reaction, scores it,
and bets the continuation.

## How a run works

Every `AUTORUN_INTERVAL_MINUTES` (5 on the VM) during 04:00–19:00 ET:

```
Nasdaq earnings calendar (BMO/AMC/DAY, EPS estimate/actual/surprise)
        │
   drift candidates — recently-public reporters with a material reaction (≥1.5%)
        │
   anti-double-dip — drop held symbols + 24h re-pick cooldown
        │
   quant scoring — top 40 movers, batched price/options/fundamental context,
   one batched moves_since_report, 8-way concurrent scoring
        │
   composite = 6 weighted signals → −100..+100 → direction (LONG/SHORT)
        │
   risk rails — max open positions, per-sector·direction concentration,
   daily-loss stop, session-aware sizing
        │
   ATR plan → book → funnel (why-picked / why-dropped)
        │
   watchers — tiered exits (target / trailing / stop / spike / stale / session-close)
        │
   grader — realized exit = the grade (alpha vs SPY, net friction)
```

The desk also **pre-arms** upcoming reporters (pre-report close + options-implied
move stored ahead of release) so the moment a report goes public the reaction is
measured instantly.

## The signals

Each signal returns −100 (strong SHORT) to +100 (strong LONG), weighted into a
composite:

| Signal | Weight | What it measures |
|--------|--------|------------------|
| `earnings_drift` | 0.30 | reaction + drift continuation vs the gap (underreaction gauge) |
| `volume_expansion` | 0.20 | post-report volume confirms real new information |
| `sector_divergence` | 0.15 | company-specific move vs sector rotation |
| `short_interest_risk` | −0.10 | squeeze/borrow penalty for SHORTs, fuel for LONGs |
| `price_structure` | 0.15 | trend strength vs ATR exhaustion |
| `liquidity` | 0.10 | the drift sweet spot ($0.5B–$10B, enough volume) |

Weights are **learned** (`quant/calibrate.py`): online nudges after every graded
outcome, batch re-calibration from the last 200 closed trades, plus exit-parameter
recommendations (widening the stop if >60% of exits are stops, etc.).

## Trading model

- **Session-scoped trades.** Each market window is its own trade: PRE 4:00–9:30,
  OPEN 9:30–16:00, AFTER 16:00–20:00. A pick entered in a session exits at that
  session's close — **nothing carries into another market**. Night is not tradeable;
  a pick decided at night is stamped PRE and enters at the next 4:00 open.
- **Per-market buffers** (`config.py`): `START_BUFFER_MIN` (5) skips the volatile
  open, `ENTRY_BUFFER_MIN` (15) stops entries near the close, `EXIT_BUFFER_MIN` (5)
  closes positions before the boundary. Entries: PRE 4:05–9:15, OPEN 9:35–15:45,
  AFTER 16:05–19:45. Exits: 9:25 / 15:55 / 19:55.
- **Entry** fills at the live price when found; a pick with no live trade is not
  taken. **Exit** is pure code (target/stop/trailing/spike/stale/session-close).

## Risk rails

Paper-desk circuit breakers, all env-overridable:

- `MAX_OPEN_POSITIONS` (20) — the book is capped; new entries are gated at the cap.
- `CONCENTRATION_MAX_PER_CLUSTER` (2) — max taken picks per sector·direction per day.
- `DAILY_LOSS_STOP_PCT` (10) — stop opening after that much realized loss today.
- 0.5× conviction sizing in thin PRE/AFTER sessions.

Every trigger records a funnel/skip reason so it's visible, never silent.

## Ledger and grading

Every evaluation is one row in `~/.alphadesk/ledger.db` (SQLite/WAL). The grader is
pure code:

- **Entry.** Closed-market picks fill at the next open; broker-filled picks use the
  actual fill. Entry precedence: broker fill → live price → Model-A open.
- **Outcomes.** `alpha_net` = directional return − SPY − friction (doubled for
  low-liquidity names). `alpha_adj` = the same plus beta-adjustment and short-borrow.
- **Realized exits are the grade.** A closed session trade is stamped `alpha_net` +
  `graded_at` at exit, so the forward grader never re-grades it.
- **Paths.** MFE/MAE from daily high/low over the hold window.
- **Anti-survivorship.** Gate-dropped reporters (reaction A/B) and quant drops
  (day-deduped skips) are all graded forward.

## Backtesting

`python -m alphadesk.main backtest --days 90` replays past earnings with the same
entry/benchmark/friction model as a live pick, bucketed by reaction size, session,
direction — and with `--selection`, graded in the composite's direction by score.
Reads a **local daily-price cache** (`price_daily` table): backfill once, then
re-runs take ~1 minute instead of being yfinance-rate-limited.

## Quick start

```bash
pip install -r requirements.txt

# Web dashboard + autorun + watchers + grader
python -m alphadesk.main dashboard        # http://localhost:8000

# One-shot headless run
python -m alphadesk.main desk

# Rebuild the UI (React 19 + TS + Vite → app/static/)
cd alphadesk/ui && pnpm build
```

## Commands

```bash
python -m alphadesk.main dashboard    # FastAPI + SPA + autorun + watchers + grader
python -m alphadesk.main desk         # one-shot run
python -m alphadesk.main grade        # grade due picks / MFE-MAE
python -m alphadesk.main status       # ledger summary
python -m alphadesk.main backtest     # does drift pay on history? (--selection)
python -m alphadesk.main abtest       # reaction-gate A/B
python -m alphadesk.main alpha        # alpha_net vs beta-adjusted alpha_adj
python -m alphadesk.main earnings     # refresh calendar, show upcoming/recent
```

## Configuration

Set via `.env` or environment.

**Required**

```ini
ALPACA_API_KEY=...            # market data + tradable universe
ALPACA_SECRET_KEY=...
```

**Key knobs** (all optional, defaults shown)

```ini
ALPHADESK_DATA=~/.alphadesk
AUTORUN_INTERVAL_MINUTES=5
AUTORUN_START_ET=04:00
AUTORUN_END_ET=19:00
MATERIAL_REACTION_PCT=1.5
QUANT_PREFILTER_MIN_SCORE=5.0
QUANT_SCORE_CANDIDATES=40
MAX_OPEN_POSITIONS=20
CONCENTRATION_MAX_PER_CLUSTER=2
DAILY_LOSS_STOP_PCT=10
START_BUFFER_MIN=5
ENTRY_BUFFER_MIN=15
EXIT_BUFFER_MIN=5
PAPER_TRADING=0              # route picks to Alpaca PAPER for real fills
PM_BASE_USD=1000
PM_MAX_POSITION_USD=2500
ALERTS_WEBHOOK_URL=          # Telegram/Slack/Discord incoming webhook
SHORT_BORROW_APR=2.0         # honest-alpha borrow charge
SHORT_BORROW_APR_ILLIQUID=30.0
```

## Repository layout

```
alphadesk/
  config.py            sessions, buffers, risk rails, tradable universe
  main.py              CLI + 5 async loops (grader, earnings+arm, autorun, watcher, quant watcher)
  ingest/
    earnings.py        Nasdaq calendar → drift candidates + pre-earnings + pre-arm
    prices.py          price context, options IV, sector ETFs, macro
  quant/
    signals.py         6 weighted signals → composite → direction
    calibrate.py       online + batch weight learning
    watcher.py         tiered exits (TP/trail/stop/spike/stale/session-close)
    stream.py          Alpaca WebSocket live prices
  desk/
    stream.py          Find Trades pipeline (candidates → risk rails → score → book)
    workflow.py        research_run() batch twin
    plan.py            ATR-based entry/target/stop
    portfolio.py       opt-in Alpaca paper order routing + reconcile
  ledger/
    store.py           SQLite/WAL: picks, runs, funnel, skips, earnings, price_daily
    grader.py          forward grading vs SPY + MFE/MAE + reaction A/B
    backtest.py        replay history (local price cache)
  app/
    dashboard.py       FastAPI: /api/* + SPA
    alerts.py          webhook notifications
  ui/                  React 19 + TS + Vite → app/static/
```

## Design principles

- **Code owns facts, physics, safety, and scoring; signals own judgment.** No LLM,
  no hardcoded narrative. Price exits are code; grading is pure arithmetic.
- **Forward-only evidence.** Every pick declares direction + horizon and is graded
  at that horizon vs SPY. The system earns trust from its ledger.
- **Fail safe.** A failed stage drops that candidate. Never a phantom pick.
- **Anti-survivorship.** Rejected candidates and skips are graded forward too.

## Status

Early and **unproven.** As of 2026-08-07: backtests (1700+ reports) and 300+ live
exits land near-zero-to-negative — the drift edge is not validated. The composite
**selection** adds value in a narrow pocket (score 5–10, OPEN session, LONGs), while
high scores, PRE/AFTER sessions, and SHORTs lose consistently. The desk is running
all sessions to build the live sample before tuning toward that pocket. Everything
is instrumented (performance page, backtest, funnel, risk rails) to make that
decision data-backed.

## Disclaimer

For educational and informational purposes only. **Not financial advice.** This
system does not place trades. Algorithmic trading carries significant risk of loss.
