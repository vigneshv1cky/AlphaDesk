# CLAUDE.md

Guidance for AI coding agents working in this repo. **This describes a HUMAN-
OPERATED terminal.** Two architectures were removed before it: the LLM
multi-agent system (`11263ae`, 2026-08-07) and every trading bot
(2026-08-16). There are currently zero LLM calls and zero autonomous trades.

## What this is

**AlphaDesk** — a research and execution terminal for ONE human trader. **There
are no trading bots.** Every autonomous decision path was deleted on
2026-08-16 (see below); a trade enters this system exactly one way, through
`POST /api/picks/manual` when a person clicks Book.

What the machine still does is the work a person can't: watch data
continuously, manage exits on positions you opened, and keep an honest score.

- **You decide.** The `/trade` page gives you candles + RSI-9 + MACD(12,26,9),
  a data-quality verdict, and a booking form that demands a written thesis.
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

## Architecture

```
ingest/earnings.py   Nasdaq calendar → watchlist, UNFILTERED (-3 to +5 days
                     around the report). A human reading the terminal judges.
ingest/prices.py     price context (Alpaca live + yfinance), intraday RSI-9 +
                     MACD + _coverage_stats(), get_chart_series(), options IV,
                     sector ETFs, macro
quant/signals.py     6 weighted signals → composite (OFFLINE ONLY: backtest,
                     grader, dashboard display — nothing trades on it)
quant/calibrate.py   online + batch weight learning from graded outcomes
quant/watcher.py     tiered exits (TP/trail/give-back/stop/spike/stale/
                     RSI-reversal/session-close) — manages YOUR positions
quant/stream.py      Alpaca WebSocket live prices (SPY registered)
desk/plan.py         ATR-based entry/target/stop + level-crossing resolution
desk/portfolio.py    position CLOSING + reconcile (entry routing deleted)
ledger/store.py      SQLite/WAL ledger + funnel/skips + price_daily cache
ledger/grader.py     forward grading vs SPY (alpha_net/alpha_adj), MFE/MAE
ledger/backtest.py   daily-bar drift research (uses the local price cache)
ledger/rsi_backtest.py  intraday replay of the RETIRED entry engine
app/dashboard.py     FastAPI: /api/* incl. /api/chart + /api/picks/manual + SPA
app/alerts.py        webhook notifications (ALERTS_WEBHOOK_URL)
main.py              CLI + 5 async loops (grader, earnings+arm, position watch,
                     quant watch, daily summary) — NO entry loop
ui/                  React 19 + TS + Vite → app/static/
                     (/live /trade /history /open /earnings /performance /system)
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

## Honest status (2026-08-16)

- **Nothing trades itself.** The terminal is the product; the operator is the
  strategy. There is no validated edge in this repo — two autonomous attempts
  were measured and both were flat-to-negative (numbers at the top).
- **Phase 0 shipped:** charts, indicators, data-quality gating, manual booking,
  automated exits on human positions, human-vs-machine scoring.
- **Planned next** (not built): filings/document workspace over free EDGAR
  full-text, with click-to-source attribution on every AI-produced claim, then
  an agentic research layer, then news theme grouping. Attribution is a
  cross-cutting rule, not a phase — no claim renders without its source.
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
