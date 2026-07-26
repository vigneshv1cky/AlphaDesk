# AlphaDesk

A predictive **multi-agent stock research engine**. You trigger a run; it reads earnings
drift + financial news, a **team** of specialized LLM agents debates the best opportunities
live, a **Head** ranks them head-to-head, and every call is written to a self-grading ledger
that scores itself forward against SPY.

**Research / paper only  --  no order execution.** Supports Claude (via `claude-agent-sdk`),
DeepSeek, and Kimi as LLM backends.


## Table of contents

- [The idea](#the-idea)
- [Alpha thesis](#alpha-thesis)
- [How a run works](#how-a-run-works)
- [The agent team](#the-agent-team)
- [LLM backends](#llm-backends)
- [LLM guardrail stack](#llm-guardrail-stack)
- [Macro awareness](#macro-awareness)
- [Extended-hours execution](#extended-hours-execution)
- [Hedging](#hedging)
- [Ledger and grading](#ledger-and-grading)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Configuration](#configuration)
- [Repository layout](#repository-layout)
- [Design principles](#design-principles)
- [Status](#status)
- [Disclaimer](#disclaimer)


## The idea

Markets price a headline in seconds, but the *consequences* take days to propagate. A supplier
hasn't repriced yet. A theme is still building. A big move has more room to run. AlphaDesk
hunts those lags  --  and keeps score of whether it was right.

It is a simulated research desk: a scout allocates attention, specialists write briefs, a
researcher argues a thesis, a critic attacks it (and may flip it), a judge rules, a head
ranks. Then reality grades every call at its pre-committed horizon.


## Alpha thesis

Three slow-digestion edges:

- **MOMENTUM**  --  big moves (especially post-earnings) continue for days; bet the continuation
  in the direction of the *reaction*, not the *result*.
- **SPILLOVER**  --  a shocked company reprices instantly; its suppliers, customers, and
  competitors drift for days. The **Connections desk** maps the shock to connected,
  still-unmoved names.
- **THEME**  --  investment themes build over days; mention-velocity leads the crowd.

Every pick declares `direction · horizon · edge · confidence` and is graded at exactly that
horizon vs SPY, net of friction. Horizon is pre-committed per edge (default 1 day), not
chosen by the judge  --  no garden-of-forking-paths.


## How a run works

Clicking **Find Trades** streams the full pipeline live to the terminal:

```
Earnings drift + financial news + optional GDELT world news
        │
   [Position review]  re-check open picks on fresh news only (price-blind reviewer)
   [Macro check]      VIX spike / rate move? → review + hedge + scout beneficiaries
        │
   [Anti-double-dip]  drop held names, same-story within cooldown
   [Connections desk] top-N shocks → spillover candidates (code-first: EDGAR + Polygon)
        │
   SCOUT  ── picks ≤5, reason for every pick and skip (materiality-ranked)
   GATE   ── drop picks with no verifiable external catalyst (fail-open)
        │  per surviving pick, in parallel:
   NOTES  (market + news) + earnings evidence + calibration scorecard
        │
   RESEARCHER → CRITIC → code fact-check → RESEARCHER rebuttal → JUDGE
   PLAN   entry / target / stop
   every Nth pick → LONER control arm
        │
   HEAD   head-to-head ranking, concentration cap per sector+direction
        │
   LEDGER (SQLite/WAL) → GRADER (hourly, alpha_net + alpha_adj vs SPY)
   POSITION WATCHER (~180s)  --  bar-based first-touch exit (pure code)
```

Every step streams to the browser in a terminal-style UI, so you watch the desk debate in
real time.


## The agent team

| Role | Tier | Job |
|------|------|-----|
| **Scout** | sonnet | Sees every news-active symbol + price context. Picks ≤5, reasons for every pick and skip. |
| **Gate** | haiku | Pre-debate catalyst screen. Drops picks with no real external catalyst. Fail-open. |
| **Notes** | haiku | Two parallel briefs: market (price + valuation + priced-in + spent-move ratio) and news. |
| **Connections** | opus | Code-first spillover desk: EDGAR 10-K disclosures + Polygon peers + news relation graph. |
| **Researcher** | sonnet | Directional thesis from briefs + track record. |
| **Critic** | opus | Attacks with evidence. Can FLIP the call, STAND_ASIDE, or SUPPORT. |
| **Judge** | opus | Always commits to LONG/SHORT. Approved = conviction call; else = thin lean. |
| **Plan** | sonnet | Entry at current price, realistic target, invalidation stop. |
| **Loner** | opus | Single-agent control arm. Off by default (`SOLO_ARM_EVERY_N=0`). |
| **Head** | opus | Head-to-head ranking across all debated ideas. Concentration cap applied. |
| **Review** | opus | Re-checks open positions on fresh news (price-blind  --  price exits are code only). |

**CHEAP_MODELS** (default ON) downgrades opus judgment roles to sonnet for cheap hourly
runs. Every pick is model-tagged so you can compare cohorts.


## LLM backends

All-or-nothing: exactly ONE provider serves every role. Set via `MODEL_PROVIDER`:

| Provider | Env var | Notes |
|----------|---------|-------|
| `claude_sdk` (default) |  --  | Requires Claude Max subscription + Claude Code CLI |
| `deepseek` | `DEEPSEEK_API_KEY` | Tier→model: opus→`v4-pro`, sonnet/haiku→`v4-flash` |
| `kimi` | `KIMI_API_KEY` or `MOONSHOT_API_KEY` | Tier→model: all→`kimi-k2.6` (k3 opt-in) |

Override per-tier models: `DEEPSEEK_MODEL_OPUS=...`, `DEEPSEEK_MODEL_SONNET=...`, etc.
Override per-role: `MODEL_CRITIC=...`, `MODEL_JUDGE=...`, etc.


## LLM guardrail stack

Every call passes through `llm.call_role`:

1. **Model resolution**  --  tier + env override + rate-limit downgrade ladder
2. **Injection defense**  --  external text wrapped in `<data:*>` delimiters; web results tagged UNTRUSTED
3. **Breaker check**  --  circuit breaker opens when bottom tier is rate-limited
4. **Input-size cap**  --  48k chars per call (cost + DoS guard)
5. **Schema validation**  --  strict JSON schema; one re-ask then safe default
6. **Universe whitelist**  --  every ticker validated against Alpaca-tradable universe
7. **Concurrency + budget**  --  semaphore caps parallel calls; tool budget per web-grounded call
8. **Token telemetry**  --  per role/model/decision, written to the ledger

Fail-safe: a failed call drops that candidate. **Never a phantom pick, never a retry storm.**


## Macro awareness

The system tracks macro conditions and reacts to dislocations in real time:

- **Macro snapshot**  --  10Y yield, Fed funds rate (13-week T-bill proxy), VIX with 1-month
  deltas. Cached every 10 minutes. Fed to every agent prompt.
- **FOMC calendar**  --  hardcoded 2026 schedule. Agents see graduated warnings: decision day
  (don't open new positions), within 3 days (size down on rate-sensitive names), etc.
- **Macro shock detection**  --  VIX spike >20% or 10Y move >15bp triggers review of all open
  positions. In PRE/AFTER hours: auto-hedge flagged longs + scout for beneficiary stocks.
- **Macro scout**  --  when a shock hits in extended hours, a single call finds stocks
  positioned to profit from the specific event (rate plays, VIX products, gold miners, etc.).


## Extended-hours execution

- **PRE (4:00–9:30 ET) / AFTER (16:00–20:00 ET)**  --  watcher runs spot-price monitoring for
  broker-filled positions. Stops/targets enforced.
- **Macro shocks in extended hours**  --  flagged longs get companion SHORT hedges; macro scout
  books profit positions before the gap.
- **OPEN (9:30–16:00 ET)**  --  full bar-based monitoring with intraday minute bars.
- **CLOSED (20:00–4:00 ET)**  --  no monitoring.


## Hedging

Two complementary mechanisms:

- **Macro hedge**  --  triggered by shock detection in PRE/AFTER. Mechanical companion SHORT
  for each flagged LONG. Target = parent's stop, stop = +3%. Auto-closed when parent exits.
- **Conviction hedge**  --  config-driven (`HEDGE_CONFIDENCE_THRESHOLD`). Below-threshold LONGs
  get a companion SHORT at booking time. Off by default (0).

Both are recorded in the ledger with `hedge_of` linking to the parent. The grader measures
whether hedging adds or costs over the sample.


## Ledger and grading

Every evaluation is one row in `~/.alphadesk/ledger.db`. The grader is pure code:

- **Entry.** Model A: closed-market picks fill at the next 9:30 open. Broker-filled picks
  (extended hours) use the actual fill price.
- **Outcomes.** `ret_horizon` at the pre-committed horizon, direction-aware. SPY over
  identical window.
- **Alpha.** `alpha_net` = directional return − SPY − friction. `alpha_adj` = same plus
  beta-adjustment and short-borrow charge (honest-alpha prototype).
- **Paths.** MFE/MAE from daily high/low over the hold window. Split-adjusted for dividends/splits.

Anti-survivorship: rejected picks, scout skips, and reaction-gate drops are all graded
forward. The ledger earns trust from its scorecard, not its prose.


## Quick start

```bash
pip install -r requirements.txt

# Web dashboard + hourly grader + auto-run loop
python -m alphadesk.main dashboard        # http://localhost:8000

# One-shot headless run
python -m alphadesk.main desk

# Rebuild the UI (React 19 + TS + Vite → app/static/)
cd alphadesk/ui && pnpm build
```


## Commands

```bash
python -m alphadesk.main dashboard     # v2: dashboard + hourly grader + auto-run + watcher (primary)
python -m alphadesk.main desk          # one-shot headless run
python -m alphadesk.main world         # one GDELT world-news tick
python -m alphadesk.main grade         # grade all due picks
python -m alphadesk.main status        # ledger summary + token usage
python -m alphadesk.main backfill      # one-shot news backfill
python -m alphadesk.main earnings      # refresh calendar; show upcoming + recent
python -m alphadesk.main abtest        # reaction-gate A/B: forward alpha vs SPY by reaction size
python -m alphadesk.main alpha         # honest alpha: beta-adjusted + borrow-aware
python -m alphadesk.main run           # legacy 24/7 scheduler
```


## Configuration

Set via `.env` file or environment.

**Required**

```ini
ALPACA_API_KEY=...            # market data + tradable universe
ALPACA_SECRET_KEY=...
POLYGON_API_KEY=...           # financial news (optional but recommended)
ADMIN_USERNAME=admin          # dashboard Basic Auth
ADMIN_PASSWORD=...
```

**LLM provider** (pick ONE)

```ini
MODEL_PROVIDER=deepseek       # claude_sdk | deepseek | kimi
DEEPSEEK_API_KEY=sk-...       # for deepseek
# KIMI_API_KEY=sk-...         # for kimi
```

**Tuning knobs** (all optional)

```ini
CHEAP_MODELS=1                # 1=downgrade opus→sonnet for cheap hourly runs (default); 0=full tiers
LEAN_MODE=1                   # 1=cost rails: earnings-primary, tighter caps (default); 0=full mode
HEDGE_CONFIDENCE_THRESHOLD=0  # auto-hedge LONGs with confidence below this (0=off)
AUTORUN_INTERVAL_HOURS=1      # auto-fire Find Trades every N hours within ET window
AUTORUN_START_ET=09:35        # window start
AUTORUN_END_ET=16:00          # window end
WORLD_MAX_CATEGORIES=0        # GDELT world news in Find Trades (0=off)
PAPER_TRADING=0               # route to Alpaca paper account (0=off)
PM_EXTENDED_HOURS=0           # extended-hours limit orders for PRE/AFTER picks
CONCENTRATION_MAX_PER_CLUSTER=2  # max TAKEN picks per sector+direction per day
SCOUT_MAX_CANDIDATES=60       # materiality-ranked candidate window size
MATERIAL_REACTION_PCT=1.5     # post-earnings drift needs ≥ this reaction
ENTRY_GAP_SKIP_PCT=2.0        # skip closed-market pick if open gapped > this from planned price
SOLO_ARM_EVERY_N=0            # Nth pick → loner control arm (0=off)
MAX_RUNS_PER_DAY=50           # runaway guard
LLM_MAX_CONCURRENCY=4         # parallel LLM calls
MODEL_<ROLE>=...              # override any role's tier, e.g. MODEL_JUDGE=opus
```


## Repository layout

```
alphadesk/
  config.py            model map, caps, sessions, tradable universe
  llm.py               guarded LLM call stack  --  every call through call_role
  ingest/
    news.py            Polygon poll → enrichment → candidates
    earnings.py        Nasdaq calendar → drift candidates (time-aware, gap vs drift)
    prices.py          real-time Alpaca + yfinance context + macro snapshot + FOMC calendar
    relations.py       SEC EDGAR 10-K + Polygon peers + news relation graph
    world.py           GDELT world-news (11-cat taxonomy, off by default)
  desk/
    stream.py          on-demand "Find Trades" SSE flow (v2 primary)
    workflow.py        research_run()  --  batch pipeline
    debate.py          shared researcher→critic→judge core (both entry points)
    scout.py           attention allocation, one prompt
    gate.py            pre-debate catalyst screen (fail-open)
    notes.py           market + news brief subagents
    team.py            researcher, critic, judge, head prompts + fact-check + concentration cap
    connections.py     code-first spillover discovery + web backstop
    plan.py            trade plan + pure-code exit physics
    loner.py           single-agent control arm
    review.py          price-blind position review
    macro.py           macro shock detection, hedging, macro scout + trade
    news_check.py      same-story vs new-catalyst check
    earnings_reader.py code-fetched earnings facts (no LLM in number path)
    portfolio.py       Alpaca paper portfolio manager (opt-in, reconciliation loop)
  ledger/
    store.py           SQLite/WAL: picks, runs, funnel, earnings, tokens, hedges
    grader.py          forward grading vs SPY + MFE/MAE  --  pure code
  app/
    dashboard.py       FastAPI + Basic Auth + SSE + SPA
    scheduler.py       hourly grader loop; legacy 24/7 loop
  main.py              CLI entrypoint + position watcher
  ui/                  React 19 + TS + Vite → built into app/static/
```


## Design principles

- **Agents own judgment; code owns facts, physics, safety, and scoring.** No hardcoded
  thresholds. The scout has no RVOL cutoff. The grader is pure arithmetic.
- **Attention is information-driven, never price-driven.** Decisions come from causes (news,
  earnings), not price narration. Price exits belong to code (watcher), news exits to the LLM
  (price-blind reviewer).
- **Forward-only evidence.** Every pick declares direction + horizon + edge + confidence and
  is graded at exactly that horizon vs SPY. The system earns trust from its ledger.
- **Fail safe.** A failed stage drops one candidate. Never a phantom pick, never a retry storm.
- **Code-first evidence.** Earnings facts, relationships, and macro data are fetched in code.
  LLMs interpret facts, they don't retrieve them.


## Status

Early and **unproven.** The ledger clock is running but the sample is tiny (~28 graded as of
2026-07-22, direction ≈ coin-flip, mean alpha negative). The calibration prior and kill
criteria stay dormant until the sample is large enough.

Recent work (2026-07-26): switched to DeepSeek, added macro awareness (FOMC calendar, shock
detection, hedging), extended-hours monitoring, and redesigned the UI as a terminal-style
console. The system is ready to accumulate a real sample.


## Disclaimer

For educational and informational purposes only. **Not financial advice.** This system does
not place trades. Algorithmic trading carries significant risk of loss.
