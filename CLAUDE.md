# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**AlphaDesk** — a predictive multi-agent stock research engine. You trigger a run
("Find Trades"); it reads a wide window of financial news + earnings (world news
optional, off by default), a **team** of specialized LLM agents debates the best
opportunities live, a **Head** ranks
them head-to-head, and every call is written to a self-grading ledger that scores
itself forward against reality. **Research / paper only — no order execution.**

> **Plain-word vocabulary (2026-07-18):** the agent roles were renamed from
> trading jargon to plain words. Old→new: Triage→**Scout**, Analyst→**Researcher**,
> Skeptic→**Critic**, Arbiter→**Judge**, Chief→**Head**, Solo→**Loner**,
> Exposure→**Connections**. DB codes: arm COMMITTEE→**TEAM**/SOLO→**LONER**;
> edge RIPPLE→**SPILLOVER**/NARRATIVE→**THEME**/DRIFT→**MOMENTUM**/WORLD_EVENT→**WORLD**;
> verdict CONFIRM→**STRONG**/WEAKEN→**SOFT**/REJECT→**PASS**.

All LLM calls run through `llm.py`'s guarded stack, which dispatches to a
pluggable **transport** (`config.MODEL_PROVIDER`): `claude-agent-sdk` (the
bundled Claude Code CLI on a Claude Max subscription — the default), or an
OpenAI-compatible HTTP API — **kimi** (Moonshot) or **deepseek** — with API
keys. **ALL-OR-NOTHING by law: exactly ONE provider serves EVERY role in the
process** — no per-role provider mixing, no silent cross-provider fallback (a
missing key or bad provider name fails loud at import/call time, never a quiet
route back to Claude). On an HTTP provider the Claude SDK is never even
imported (lazy import inside `_one_shot_sdk`). Roles map to abstract tiers
(opus/sonnet/haiku); each provider resolves tier → concrete model
(`config.PROVIDER_MODELS`, env-overridable). v1 of the HTTP transport:
web-grounded roles (connections, earnings_reader) answer parametrically (no
tool loop yet) — a search shim plugs into `_one_shot_http` later.

> The legacy `stock_sentiment/` bot (FinBERT + AWS Bedrock) was removed 2026-07-16.
> AlphaDesk (`alphadesk/`) is the only system in this repo.

## Commands

```bash
pip install -r requirements.txt

# Web dashboard + hourly grader (v2 primary mode — trades run on button click,
# AND auto-fire every AUTORUN_INTERVAL_HOURS in the AUTORUN_START/END_ET window; default hourly 09:35–16:00 ET)
python -m alphadesk.main dashboard        # then open http://localhost:8000

# Convene the team NOW on recent news (headless, writes to ledger)
python -m alphadesk.main desk

# One GDELT world-news tick
python -m alphadesk.main world

# Grade due picks / print the scorecard / one-month news backfill
python -m alphadesk.main grade
python -m alphadesk.main status
python -m alphadesk.main backfill

# Reaction-gate A/B: forward alpha vs SPY bucketed by reaction size (is the gate
# filtering noise or cutting quiet under-reactions? also reveals the right threshold)
python -m alphadesk.main abtest

# Honest alpha: SPY-relative alpha_net vs beta-adjusted + borrow-aware alpha_adj
# (how much apparent edge was really beta exposure / unpriced short borrow)
python -m alphadesk.main alpha

# Legacy autonomous 24/7 scheduler (kept, not the v2 path)
python -m alphadesk.main run

# Rebuild the web UI (React → alphadesk/app/static/)
cd alphadesk/ui && pnpm build
```

## Design laws (every module obeys these)

1. **Agents own judgment; code owns facts, physics, safety, and scoring.** No
   hardcoded judgment thresholds — the scout has no RVOL cutoff, the score has no
   formula. Code owns arithmetic, hard facts (tradability), and rails (caps,
   injection defense, schema validation).
2. **Attention is information-driven, never price-driven.** Price *informs* a
   decision; it never *triggers* one. Decisions come from causes (news), not
   price-narration.
3. **Forward-only evidence.** Every pick declares `direction · horizon_days(1–10)
   · edge · confidence` and is graded at exactly that horizon vs SPY, net of
   friction. The system earns trust from its ledger, not its prose.

## Alpha thesis — three slow-digestion edges

- **SPILLOVER** — a shocked company reprices instantly; its suppliers/customers/
  competitors drift for days (the Connections desk finds the connected, unmoved names).
- **THEME** — investment themes build over days; mention-velocity leads the crowd.
- **MOMENTUM** — big moves continue for days; bet the continuation.

## Architecture

Two **entry points** run the same team (they have partially diverged — see
Tech debt):

- `desk/stream.py` — the on-demand **"Find Trades"** SSE flow (dashboard button).
  **v2's primary path.** Streams the agents' deliberation live to the browser.
- `desk/workflow.py` — `research_run()`, the pure batch pipeline (the `desk` CLI,
  the scheduler's autonomous mode, and future replay). Returns ledger IDs only.

### Pipeline

```
Polygon (financial news) + earnings drift (+ since-report move) + Alpaca real-time last trade / yfinance history (price context)
        │  candidates (symbol → enriched articles)
        │  [+ GDELT world news if WORLD_MAX_CATEGORIES>0 — OFF by default]
   [Connections desk]  (expose=true) shock → 1 web-grounded opus call → spillover candidates
        │
   SCOUT (sonnet)  ── picks ≤5, reasons for every pick AND skip
        │
   GATE (haiku)  ── drop picks with no real external catalyst BEFORE the debate (fail-open;
     EARNINGS-sourced picks AUTO-PASS — a confirmed report is the catalyst, no call needed)
        │  per surviving pick, in parallel:
   2 NOTES (haiku): market (price+valuation+priced-in, incl. realized-vs-implied "spent move" ratio) · news
   + calibration prior (the desk's own graded scorecard, sample-gated at 8 trades)
        │
   RESEARCHER (sonnet) → CRITIC (opus) → fact-check (code) → RESEARCHER rebuttal → JUDGE (opus)
   every 3rd pick → LONER (opus) control arm (kill-criterion: does the team beat one agent?)
        │
   HEAD (opus) → head-to-head ranking (TAKE-ALL mode 2026-07-24: EVERY debated pick is booked as
     a position; `approved`/ranking kept as metadata to test if selection adds value; cap still trims correlated)
        │
   LEDGER (SQLite/WAL) → GRADER (hourly, alpha_net vs SPY at own horizon; aborts the pass on a
     SPY-data outage rather than grading benchmark-less; VOIDS rows that can never grade —
     never-filled 'not-taken' past fill time, delisted symbols — instead of retrying forever)
        │
   POSITION WATCHER (~180s): walks intraday MINUTE bars for the first-touched level → close
     at that level, gap-/order-aware (PURE CODE — the only price-based exit; no LLM)
```

### Model tiering (`config.MODEL_MAP`, every role env-overridable `MODEL_<ROLE>`)

Tiers are ABSTRACT — each transport resolves them to a concrete model
(`config.PROVIDER_MODELS`): on `claude_sdk` the tier IS the CLI alias; on `kimi`
ALL tiers default to `kimi-k2.6` (the k3 flagship is off by default — opt in via
`KIMI_MODEL_OPUS=kimi-k3` or `MODEL_<ROLE>=kimi-k3`; `KIMI_K3_REASONING_EFFORT`
caps its always-on reasoning, default low); on `deepseek`
sonnet/haiku→`deepseek-chat`, opus→`deepseek-reasoner`. A `MODEL_<ROLE>` override may
also name a concrete model directly (bypasses the tier ladder).

- **haiku**: enrichment, notes/briefs, news_check, gate (high-volume extraction)
- **sonnet**: scout, researcher, earnings_reader, plan
- **opus** (in the default model map): critic, judge, loner, head, review, connections (web-grounded)

**CHEAP_MODELS mode (default ON, 2026-07-24)** — for cheap, frequent (hourly) automation the
opus judgment roles are downgraded to **sonnet**, so a full run makes NO opus calls and costs a
fraction. It's a quality/direction BET on an unproven system (sonnet judgment vs opus; and
researcher+critic now share a tier, losing the deliberate decorrelation below). Every pick is
model-tagged, so compare the cheap vs opus cohorts in the ledger. `CHEAP_MODELS=0` restores opus;
keep a single role sharp with a per-role override, e.g. `MODEL_JUDGE=opus`.

Researcher is sonnet, Critic is opus **on purpose** (in the non-cheap map) — different models
between debate roles decorrelate errors. On rate-limit each role steps down opus→sonnet→haiku
(tagged on the ledger row); if the bottom tier is limited too, the breaker opens.

## The LLM layer — `llm.py` (every model call passes through `call_role`)

Guardrails, in order: model resolution (+ downgrade ladder) · injection defense
(`wrap_data` delimiters + `_INJECTION_GUARD`; web results tagged UNTRUSTED) · input-size
cap (`LLM_MAX_INPUT_CHARS`) · schema validation + one retry, then safe default (a failed
stage drops the candidate, never a phantom pick) · **universe whitelist** (invented
tickers rejected — the key output-security limit) · concurrency semaphore
(`LLM_MAX_CONCURRENCY`) + per-tool-call `max_budget_usd`/`max_turns` (SDK transport) ·
token telemetry.

Transports (`MODEL_PROVIDER`): `claude_sdk` (default — one persistent event loop for the
CLI subprocess; per-call `max_budget_usd`) · `kimi` / `deepseek` (OpenAI-compatible
`/chat/completions` via urllib; JSON-mode `response_format`; HTTP 429 feeds the same
rate-limit ladder/breaker; per-provider base URL + key envs in `PROVIDER_ENDPOINTS`).

## File structure

```
alphadesk/
  config.py            MODEL_MAP, caps, sessions, tradable universe (weekly Alpaca cache)
  llm.py               the guarded call stack — every LLM call goes here
  ingest/
    news.py            Polygon poll → Haiku enrichment → candidates (+ persists text-evidenced relations to relation_facts)
    earnings.py        Nasdaq earnings calendar → post-earnings-drift candidates the moment a report is PUBLIC
                       (NOT gated on eps_actual, which lags a day — direction from the price reaction, not the result)
    relations.py       relationship FACTS in code: SEC EDGAR 10-K customer/supplier disclosures (FTS + entity-decoded
                       proximity parse — precise, sparse), Polygon related-companies peers, the news relation graph
    world.py           GDELT world-news (11-cat taxonomy) — OFF by default in Find Trades
                       (WORLD_MAX_CATEGORIES=0); still used by the scheduler + `world` CLI
    prices.py          lazy per-symbol context — real-time Alpaca last trade (yfinance history fallback); NO triggers, NO sweeps
  desk/
    stream.py          on-demand "Find Trades" SSE flow (v2 primary path)
    workflow.py        research_run() — batch pipeline (desk CLI, scheduler, replay)
    debate.py          deliberate() — the shared Researcher→Critic→Judge core
    scout.py           all attention judgment, in one prompt (was triage.py)
    gate.py            pre-debate catalyst screen — drop phantom setups (haiku, fail-open); screen_picks shared by both pipelines, EARNINGS picks auto-pass (a confirmed report needs no check)
    notes.py           2 parallel haiku note subagents: market (incl. realized-vs-implied spent-move ratio), news (was briefs.py)
    connections.py     the Connections desk — CODE discovers relationships (ingest/relations: EDGAR 10-K customer disclosures + Polygon peers + the news relation graph), one LLM call judges direction/strength with the evidence; web search only as the discovery backstop
    team.py            Researcher ⇄ Critic → Judge, + calibration_block, + head_ranking (was committee.py)
    loner.py           single-agent control arm (was solo.py)
    plan.py            trade plan (entry/target/stop, agent) — entry ALWAYS a market fill at the current price (no resting limits); + level_crossed / first_touch_exit / realized_exit (pure-code exit physics) + the closed-market GAP-SKIP guard
    review.py          position review — price-BLIND (fresh news only): HOLD/EXIT on open TAKEs per run; price exits belong to the watcher, never to an LLM (was reeval.py)
    portfolio.py       paper portfolio manager — OPT-IN (PAPER_TRADING) reconciliation loop that routes booked picks to an Alpaca PAPER account (conviction-weighted, idempotent)
    news_check.py      same-story vs new-catalyst check on a recently-debated name
    earnings_reader.py earnings evidence: code-fetched FACTS only (beat/miss track record, revenue trend, analyst revisions — no LLM, no confabulation)
  ledger/
    store.py           SQLite/WAL: picks (+ exit/mfe/source cols), earnings, funnel, token_usage, relationships
    grader.py          forward grading vs SPY + MFE/MAE paths + skip-grading — pure code
  app/
    dashboard.py       FastAPI + Basic Auth + SSE endpoint + static SPA
    scheduler.py       hourly grader loop (v2); legacy 24/7 loop (run mode)
  main.py              CLI entrypoint (dashboard/desk/world/grade/status/backfill/run) + position watcher (level cross → first-touch exit, pure code)
  ui/                  React 19 + TS + Vite + shadcn/ui → built into app/static/
```

## Environment variables

```ini
ALPACA_API_KEY=...            # market data + universe (paper keys fine)
ALPACA_SECRET_KEY=...
POLYGON_API_KEY=...           # financial news (optional)
MODEL_PROVIDER=claude_sdk     # ONE provider for ALL roles: claude_sdk (default, Claude Max CLI) | kimi | deepseek
KIMI_API_KEY=...              # or MOONSHOT_API_KEY — when MODEL_PROVIDER=kimi
DEEPSEEK_API_KEY=...          # when MODEL_PROVIDER=deepseek
# KIMI_BASE_URL / DEEPSEEK_BASE_URL — endpoint overrides (e.g. moonshot.cn / proxies)
# KIMI_MODEL_{OPUS,SONNET,HAIKU} / DEEPSEEK_MODEL_{OPUS,SONNET,HAIKU} — tier→model overrides
# LLM_HTTP_MAX_TOKENS=4096 — completion cap per HTTP call
# KIMI_WEB_SEARCH=1 — builtin $web_search tool loop for kimi (needs KIMI_THINKING=disabled);
#     falls back to parametric on failure. Used by the connections desk; earnings evidence
#     is code-fetched facts only (see earnings_reader — no LLM in the number path)
# NB: on HTTP providers without search, web-grounded roles answer parametrically.
ADMIN_USERNAME=admin          # dashboard Basic Auth (fail-closed if unset)
ADMIN_PASSWORD=...
ALPHADESK_DATA=~/.alphadesk   # ledger.db, universe.json, relationship cache
SOLO_ARM_EVERY_N=0            # 0=off (lean default); set e.g. 6 to measure committee-vs-solo
CHEAP_MODELS=1               # 1=downgrade the opus judgment roles (critic/judge/head/review/loner/connections) to sonnet — no opus, cheap hourly runs. 0=opus defaults. Per-role MODEL_<ROLE> overrides win
LEAN_MODE=1                  # 1=cost rails: earnings-primary news gating, tighter news/scout/debate caps, trigger-only reviews. 0=full mode. Sub-knobs below each override individually
LEAN_EARNINGS_SKIP_NEWS=5    # ≥ this many material drift reporters → skip the news poll entirely (earnings-primary run)
LEAN_NEWS_HOURS=12           # news window cap when the poll does run (full: 24/48h)
LEAN_NEWS_MAX_ARTICLES=100   # Polygon fetch cap (full: 200)
LEAN_NEWS_MAX_SCAN=200       # raw Polygon scan cap (full: 400)
LEAN_SCOUT_MAX_CANDIDATES=30 # scout window cap (full: 60; materiality ranking keeps the big movers)
LEAN_MAX_DEBATES=4           # debates per run (full: 6)
LEAN_REVIEW_TRIGGER_ONLY=1   # review an open position only on fresh news in the pool; else auto-HOLD (the 180s watcher guards target/stop in code, so nothing is unguarded)
PAPER_TRADING=0              # 1=route booked picks to an Alpaca PAPER account (desk.portfolio reconciliation loop). OFF by default — nothing trades until you opt in
PM_BASE_USD=1000             # conviction-weighted sizing: $ for a conviction-50 pick, scaled by adjusted_score
PM_MAX_POSITION_USD=2500     # cap per position
PM_MAX_POSITIONS=20          # max concurrent Alpaca positions (best conviction first)
PM_EXTENDED_HOURS=1          # 1=PRE/AFTER window picks route as LIMIT orders at the decision price (extended_hours=True); the broker's actual fill is stamped back (broker_fill_price/ts) and becomes the ledger entry (grader + watcher prefer it). 0=all Model A (fills at the open)
WORLD_MAX_CATEGORIES=0        # GDELT world news in Find Trades: 0=off (default); 4=full sweep every ~3 runs; 11=every run (slow)
MATERIAL_REACTION_PCT=1.5     # earnings drift needs a visible reaction to be a directional candidate; below this % (live vs pre-report close) = skip
REACTION_AB_HORIZON_DAYS=3    # shadow A/B: forward-grade EVERY reporter's reaction (passed AND dropped) over this horizon → `abtest` shows if the gate cuts winners
SHORT_BORROW_APR=2.0          # honest-alpha prototype: annual % borrow charged to SHORTs over the hold (easy-to-borrow baseline)
SHORT_BORROW_APR_ILLIQUID=30.0 # higher borrow for low-liquidity shorts (hard-to-borrow proxy until a real borrow feed exists)
CONCENTRATION_MAX_PER_CLUSTER=2 # max TAKEN picks per correlation cluster (sector+direction) per day; excess correlated picks recorded but not booked
EDGE_HORIZON_MOMENTUM=1        # PRE-COMMITTED grading horizon (fixed in advance, not judge-chosen). SHORT-HORIZON daily mode: ALL edges = 1 (strictly today→tomorrow)
EDGE_HORIZON_SPILLOVER=1       # SPILLOVER/THEME/WORLD also 1: multi-day nature handled on the INPUT (lookback) side, not the forward horizon; DEFAULT_EDGE_HORIZON_DAYS=1
ENTRY_GAP_SKIP_PCT=2.0         # always enter at the current price (market); a CLOSED-market call whose open gapped >this% from the planned price is NOT taken (stale). 0=off
SCOUT_MAX_CANDIDATES=60        # how many (materiality-ranked) candidates reach the scout per run; raise for wider coverage (more tokens/fetches)
AUTORUN_INTERVAL_HOURS=1       # dashboard mode auto-fires Find Trades every N hours within the window below (trading days); restart-safe (interval off the ledger's last run — EVERY run is recorded, incl. empty/failed ones, so the gate can't spin). <=0 = off
AUTORUN_START_ET=07:00        # window start (default 09:35; 07:00 adds the pre-open window — decisions anchor pre-market, fill at the open with the gap-guard)
AUTORUN_END_ET=19:00         # window end (default 16:00; 19:00 adds the AMC-earnings reaction window — decisions anchor on the extended-hours reaction, fill at next open). Hourly is cheap: the 24h repick cooldown means each run debates only NEW catalysts. Every pick is session-stamped; stats()/alpha bucket performance BY SESSION (PRE|OPEN|AFTER|CLOSED)
```

## Key design notes

- **No order execution** — research/paper only until the ledger earns it.
- **Lean mode (default ON, 2026-07-24)** — cost discipline while the system is unproven.
  Earnings drift is the primary, cheapest, highest-signal channel, so: (1) a heavy earnings
  slate (≥`LEAN_EARNINGS_SKIP_NEWS` reporters) skips the news poll entirely — earnings-primary
  runs; (2) when news does poll, the window/fetch/scan are capped tighter; (3) the scout
  window and debates-per-run shrink (materiality ranking means the cut is the tail, not the
  movers); (4) open-position reviews need a TRIGGER (fresh news in the pool) — no trigger is an
  auto-HOLD, and the 180s watcher still closes on level crosses, so nothing is unguarded.
  `record_ingest` still logs the skipped poll (0 articles) so the source scorecard reflects
  the throttling. `LEAN_MODE=0` restores full mode; every knob overrides independently.
- **Self-improvement is grounded, not RL**: a numeric calibration scorecard is fed
  into agent prompts (dormant until ~8 graded trades); the real self-correction is the
  pre-committed **kill criteria** (drop the debate / an edge / the team if the
  ledger says they don't pay). No free-form "lessons" memory (persistent injection risk).
  NB: this is NOT the agent learning — the model is frozen; the loop builds evidence so
  *humans* retune. In-context feedback changing behavior is itself an unproven experiment.
- **Anti-survivorship** — the ledger grades NOT-TAKEN picks (counterfactuals: keyed on
  `taken=0`, NOT `approved=0` — in TAKE-ALL mode approved=0 picks are booked positions,
  so counting them as 'missed winners' would double-count the main scorecard and tell
  the desk it passed on things it holds), and
  `grader.grade_skips()` grades scout SKIPS too (directionless: a move vs SPY over
  `SKIP_GRADE_DAYS` above `SKIP_MISS_ABS_ALPHA`% = a dislocation we ignored). `team.
  false_negative_block()` feeds the not-taken/skip miss-rate into scout + judge — sample-
  gated, removable, tagged as an experiment.
- **Miss diagnosis is conversational** — ask Claude "why did we miss X?"; it traces
  `store.symbol_traces` / `symbol_skips` and fixes data/prompt/bug. No UI tool for it.
- **Pre-committed horizon** — the grading horizon is FIXED per edge in advance
  (`config.pinned_horizon`, `EDGE_HORIZON_DAYS`), NOT chosen by any agent after seeing the
  setup. SHORT-HORIZON daily-run mode (2026-07-24): ALL edges = 1 (strictly today→tomorrow) —
  the desk runs every day, so the forward CALL is always 1-day. The multi-day nature of
  SPILLOVER/THEME/WORLD is handled on the INPUT/lookback side (detect the buildup from days of
  history — price 5d/20d/90d is already baked into every candidate; THEME mention-velocity keys on the
  news window), NOT the horizon. Read the slow signal, bet the next day. Bump any edge via env. `debate.deliberate` computes `horizon =
  pinned_horizon(edge_hint)` UP FRONT and hands it to EVERY role (researcher, critic, rebuttal,
  judge — and the trade PLAN, so entry and grade stay consistent): no agent picks a horizon,
  the researcher/critic schemas carry no horizon field, and each prompt states the fixed window
  to argue within (a thesis needing longer → weaker call/PASS, not a stretched clock). The loner
  control arm is pinned to the SAME horizon in BOTH entry points (stream + workflow) for an
  apples-to-apples comparison. Removes the garden-of-forking-paths
  (a catalyst bookable as a 1d or 10d call, only the chosen spec logged) so alpha_net is an
  honest out-of-sample number — and neutralises the horizon-shop the calibration buckets fed.
- **Concentration cap** — `team.apply_concentration_cap` (run in `stream.py` after the Head
  ranks, before `mark_taken`) tags every pick with a correlation CLUSTER (sector|direction)
  and caps TAKEs at `CONCENTRATION_MAX_PER_CLUSTER` per cluster per day (counting earlier runs
  today). Excess correlated picks are un-taken — still recorded and direction-graded (anti-
  survivorship), just not booked as a live position. Fixes BOTH the concentrated real risk (5
  same-sector same-direction names on one driver = 5× exposure) and the ledger counting one
  clustered bet as many independent wins: `stats.effective_graded` dedups the graded sample to
  distinct clusters (shown as "N independent" in the Track record; keeps the Head-ranked best
  of a cluster). Sector from `get_fundamentals` (cached); unknown-sector picks aren't clustered.
- **Honest-alpha prototype (beta + borrow)** — the grader books `alpha_net` (SPY-relative,
  net friction) AND, alongside it, `alpha_adj`: the same but with the benchmark BETA-adjusted
  (`ret − beta·spy_ret`, beta from trailing daily returns, clamped [0,3]) and a SHORT BORROW
  charge (annualized `SHORT_BORROW_APR[_ILLIQUID]` prorated over the hold; low-liquidity =
  hard-to-borrow proxy). Non-destructive — beta=1 and no borrow makes `alpha_adj == alpha_net`.
  `alpha` CLI shows the "beta drag" (how much apparent edge was really beta/borrow). It exists
  because the SPY-only benchmark booked beta as alpha and shorts were graded as freely
  borrowable — both inflating the read. A real borrow-rate feed and a shortability GATE (drop
  non-shortable shorts) are the follow-ups; this is the measurement, not yet an execution rail.
- **The material-reaction gate is A/B-tested, not assumed** — the gate that drops
  earnings reporters with a sub-`MATERIAL_REACTION_PCT` reaction could be filtering noise
  OR discarding the quiet under-reactions that ARE the drift edge. So `earnings.
  drift_candidates` logs EVERY public reporter's reaction (passed AND dropped) to
  `earnings_reactions` — stamped with the MARKET session at sighting (`mkt_session`) and
  the live price when sighted mid-session, so the entry clock is identical to a booked
  pick (rows predating that stamp enter at the next open, skipping day-1 — read old
  graded rows accordingly) — and `grader.grade_reactions()` forward-grades both arms vs
  SPY in the reaction direction (same Model-A entry + benchmark + friction as booked
  picks) over `REACTION_AB_HORIZON_DAYS`. `abtest` buckets the graded rows by reaction
  size: if forward alpha turns on at the threshold the gate is justified (and shows the
  right threshold); if the dropped arm pays as well, the gate is cutting winners. No LLM
  cost — a simultaneous, same-tape shadow A/B, dormant as evidence until the sample is real.
- **Spent-move read lives at ENTRY only** — at entry the market note gets an explicit
  realized-vs-implied ratio (today/5d move ÷ options-implied move) plus the earnings
  since-report move, so a fully-repriced setup reads "spent → pass" (the fix for entering
  a gap that already happened). The EXIT side has no spent-move judgment: price exits are
  owned entirely by the pure-code first-touch watcher (a spent move closes AT its target;
  a wrong one at its stop), and the reviewer is shown no prices — it exits only on fresh
  adverse NEWS (design law #2 applied to exits: price exits by code, thesis exits by
  information). The entry ratio only fires where options data exists (liquid names);
  thin names fall back to the qualitative priced-in read.
- **Gap vs capturable drift** — `prices.moves_since_report` splits the move since a
  report into the uncapturable overnight **gap** (pre-report close → first post-report
  OPEN — repriced before you could act) and the **drift** (from that open — what you
  could actually trade). Entry candidates, the Calendar "Move" column, and the true/
  false-miss verdict all key on the **drift**, so a pure-gap reprice isn't counted as a
  tradeable miss. NB: the exit/hold side is unchanged — a position held *through* a gap
  DID capture it, so its P&L/MFE still measure from the original entry.
- **Same-day earnings visibility** — the drift pool (`store.recently_reported`) is NOT
  gated on `eps_actual` (Nasdaq backfills it ~a day late, which hid every same-day
  reporter); a reporter becomes a candidate the moment it's PUBLIC (time-aware, past its
  9:30/16:00 boundary) and its direction comes from the price reaction. Run Find Trades
  just AFTER 9:30 so BMO reporters are public.
- **Scout coverage is MATERIALITY-ranked** (2026-07-24) — the scout can't see every reporter
  on a heavy day, so which ones reach it matters. The candidate window is ranked by
  `stream._materiality` (biggest earnings REACTION, else news intensity), NOT market cap, then
  capped at `SCOUT_MAX_CANDIDATES` (60). Fixes the THRM +22.7% miss: a small-cap mover no longer
  gets truncated behind mega-caps with tiny reactions. `earnings.drift_candidates` exposes
  `reaction_pct` per candidate for the rank. Raise the cap for more coverage (more tokens/fetches).
- **Extended-hours execution (opt-in, PM_EXTENDED_HOURS)** — Alpaca has no night
  session (extended hours are 4:00–20:00 ET weekdays only, and market orders don't
  fill off-hours). PRE/AFTER-window picks route as LIMIT orders at the decision price
  with `extended_hours=True`; the fill-sync pass stamps the broker's actual fill
  (`broker_fill_price/ts`) and it becomes the ledger's entry — grader benchmarks SPY
  from the last close before the fill moment, and the watcher monitors off that entry,
  so broker and ledger never disagree. CLOSED-window picks still queue for the open
  (Model A). Day-session picks still use market orders.
- **Position review (exits)** — the team only opens positions; two things close them
  early, all research/paper (a ledger `exit_ts`/`exit_reason` stamp, never an order):
  (1) each Find Trades run, BEFORE hunting new trades, the `review` agent re-checks
  open TAKEs (`store.open_taken_picks`) — **shown NO prices at all** (not entry, not
  current, not move, not momentum): it judges the thesis vs FRESH NEWS only and exits
  solely on fresh adverse information (a quiet tape is an auto-HOLD). Price-based exits
  belong to code, never an LLM; (2) the **position watcher** (`main._position_watch_loop`,
  ~180s) walks the intraday MINUTE-bar path (`prices.intraday_bars` →
  `plan.first_touch_exit`) and closes at the FIRST level actually touched — priced at that
  level, gap-aware (a level opened-through fills at the bar open), and order-aware (a bar
  spanning both target and stop books the adverse one); falls back to the spot-quote level
  check only when bars are unavailable (pure code, the ONLY price-based exit). A spent
  move closes AT its target; a wrong one at its stop; anything between keeps working to
  the levels or the horizon. HOLD is always the fail-safe default.

## Tech debt / honest status

- **Team core is converged** (`desk/debate.py`): both entry points run the same
  `deliberate()` async generator for the researcher→critic→judge→ledger-write sequence,
  the same notes (market + news), the same gate (`gate.screen_picks`), and the same
  scout-window helpers (`scout.headline_rows` / `scout.avg_sentiment`), so they no
  longer drift. The loner arm is lightly duplicated between them but pinned to the
  same horizon + inputs in both.
- **Unproven.** The ledger clock is running but the sample is tiny and shows **no edge
  yet** (~28 graded as of 2026-07-22, direction ≈ 43% ≈ coin-flip, mean alpha negative —
  statistically indistinguishable from zero). The calibration prior and kill criteria stay
  dormant until the sample is large enough. A **stale-price bug** (fixed 2026-07-22) had
  inflated early paper-exit P&L and priced-in reasoning by anchoring to yfinance's stale
  daily close the morning after an earnings gap; the forward grade (`alpha_net`) was never
  affected (it enters at the real next-session open). Highest-value next step: let the
  current honestly-priced cohort grade to a real read before changing anything.

## Current state (2026-07-27 — Jul 27)

### Deployment
- **AlphaDesk**: GCP VM `alphadesk` at 34.182.195.6:8000, project `alphadesk-research`
- **Altavela**: GCP VM `altavela` at 35.221.39.188:8001, project `altavela-research`
- Both VMs: UTC timezone, ET = UTC-4. Journal timestamps are UTC. Autoruns, dashboard, sessions all use ET.
- `MODEL_PROVIDER=deepseek`, DEEPSEEK_MODEL_SONNET=deepseek-v4-flash, DEEPSEEK_MODEL_OPUS=deepseek-v4-pro
- No CI/CD — deploy via `gcloud compute scp` + `systemctl restart`

### Active config on VM (`/opt/alphadesk/.env`)
```
CONCENTRATION_MAX_PER_CLUSTER=999   (disabled)
SCOUT_MAX_CANDIDATES=999            (all reporters)
LEAN_SCOUT_MAX_CANDIDATES=999       (no lean cap)
MAX_PICKS_PER_WINDOW=999            (env configurable, was hardcoded 5)
LEAN_MAX_DEBATES=999                (no debate cap)
AUTORUN_START_ET=04:00
AUTORUN_END_ET=19:00
AUTORUN_INTERVAL_HOURS=1
WATCH_INTERVAL_S=60
WORLD_MAX_CATEGORIES=0
PAPER_TRADING=0
```

### Bugs fixed today (Jul 27)
1. **Concentration cap `=0` blocked every pick** — `apply_concentration_cap` treated `0 >= 0` as "cap exceeded." Fixed: `<= 0` disables cap.
2. **Autorun never fired** — `next_slot` formula had `+1` that always pointed to next slot, `now >= next_slot` never true. Fixed: removed `+1` to compute current slot. Also fixed indentation bug where `try/finally` was outside `last_slot` guard (would re-fire every 60s).
3. **PRE/AFTER picks waited for 9:30 AM** — `debate.py` only stamped `entry_price` for `sess == "OPEN"`. Fixed: `sess != "CLOSED"` — PRE/AFTER fills immediately.
4. **`live_picks()` showed `taken=0` picks** — confusing dashboard with "stopped out" picks that weren't real. Fixed: added `taken=1` to SQL.
5. **MFE understated real peaks** — used daily High only, missed intraday spikes. Fixed: `max(daily_high, exit_price)` for LONG, `min(daily_low, exit_price)` for SHORT.

### Improvements deployed today
- **Risk/reward rail**: `MIN_RISK_REWARD_RATIO=1.5` — plan rejects if reward < 1.5× risk
- **Researcher prompt**: 4-step framework (catalyst → priced-in → direction → mandatory self-check)
- **Scout prompt**: "Err on side of picking" — earnings reporters are near-automatic picks
- **Head owns take decisions** — `row["take"] = cr["take"]`, respected
- **UI**: Sessions tab removed (merged into History), exit alerts in Live, briefs collapsible, no repetition in PickSheet, MFE/MAE shown
- **All caps removed**: scout sees every reporter, picks unlimited, debates unlimited

### Known / pending
- Graded sample still tiny (~20-30), system unproven
- No Alpaca paper trading (PAPER_TRADING=0)
- No Altavela fixes ported yet
- Entry_ts on CLOSED picks shows decision time not fill time (fixed for CLOSED only)
- Scout may still miss posts based on judgment (2 true misses today)

### Memory log
- 2026-07-27: User wants cross-session memory. Tell me what to add and I'll put it here.
- VM journal times are UTC, not ET — always check.
- User prefers "regular/after-hours/pre-market/overnight" labels, not session codes.
- User wants Track Record to show only exited picks — not open, not graded-only, not not-taken.
