# CLAUDE.md

Guidance for AI coding agents working in this repo. **The docs below describe the
CURRENT pure-quant engine — the old LLM multi-agent architecture was removed in
commit `11263ae` (v2, 2026-08-07). There are zero LLM calls.**

## What this is

**AlphaDesk** — a pure-quant intraday research engine. No LLM, no agents. It
uses the Nasdaq earnings calendar as a candidate SOURCE, then enters on a
**pure RSI-9 mean-reversion signal** (see the entry-engine section below),
books session-scoped paper positions, and a self-grading ledger scores every
call forward vs SPY. **Research / paper only — no live order execution**
(Alpaca PAPER fills are opt-in and off by default).

The weighted-composite drift scoring (`quant/signals.py`) still exists but is
**offline only** now — the `desk`/`backtest` CLIs, the grader, and the
dashboard. It is not on the live entry path.

## Commands

```bash
pip install -r requirements.txt

python -m alphadesk.main dashboard      # FastAPI + SPA at :8000, entry/exit watchers + grader
python -m alphadesk.main desk           # one-shot run on recent earnings drift
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
- **Entry** fills at the live price when found; a pick with no live trade is not
  taken. **Exit** is pure code: hard target/stop, trailing stop, spike reversal,
  stale expiry, the RSI signal-reversal tier, and the session-close sweep — all
  in `quant/watcher.py`. `record_exit` stamps `alpha_net = exit alpha` +
  `graded_at` (an exit IS the grade; the forward grader never re-grades a closed
  session trade).
- **Risk rails** (`config.py`, enforced in `desk/watcher.py`):
  `MAX_ENTRIES_PER_DAY` (100, a runaway backstop not a capital control),
  `MAX_BOOKINGS_PER_SYMBOL_PER_DAY` (2 per symbol+direction), and
  `DAILY_LOSS_STOP_PCT` (10). Each trigger records a funnel/skip reason.
  `MAX_OPEN_POSITIONS` and `CONCENTRATION_MAX_PER_CLUSTER` were **removed**
  (`9497d9f`) — the book is uncapped.

## Entry engine — RSI-only (`desk/watcher.py`, 2026-08-15)

Continuous per-candidate evaluation. There is no batch scanner and no ranking:
every candidate is judged purely on its own technical setup and booked the
moment it qualifies. Replaced the old top-N `desk/stream.py` scanner (removed
2026-08-13).

- **One indicator decides everything.** RSI-9 on 1-minute Alpaca bars
  (`ingest/prices.py`'s `get_intraday_ma_context`, 30s TTL cache). RSI crossing
  **UP through 30 → LONG**; crossing **DOWN through 70 → SHORT**. The cross sets
  BOTH direction and timing — there is no trend filter voting alongside it.
- **Why one indicator:** an earlier build (`7fe768a`) paired MACD(12,26,9) as a
  direction/trend filter. Two independently-moving signals can briefly disagree
  — MACD about to flip while RSI had already crossed for the OLD regime — which
  entered trades immediately before a reversal. Dropping MACD removes that
  bug class by construction instead of patching it. **Accepted trade-off: no
  directional trend-bias check at all.** Don't re-add a second signal without
  re-solving the disagreement problem.
- It's a **CROSSING**, not "wait for the extreme" (only knowable in hindsight,
  after it's already reversed). Both crosses firing on one bar is contradictory
  data and is dropped, not arbitrated.
- **Other gates:** `MA_ENTRY_MIN_RVOL` (1.2×), `MA_ENTRY_MIN_ATR_PCT` (1.5% — a
  near-zero-volatility stock has no room to reach a meaningful target/stop), and
  the per-symbol daily booking cap. Fails **CLOSED** on missing data.
- **Exit** is the same lone indicator crossing the OPPOSITE threshold (a LONG's
  reversion completing at overbought, a SHORT's at oversold) — `quant/watcher.py`
  tier 7, wired from `main.py`'s `_quantity_watch_loop`. Fails **OPEN** (missing
  data just means the tier never fires). The hard stop is deliberately widened to
  `MA_STOP_BACKSTOP_ATR` (4.0× ATR) — a rarely-triggered backstop for a violent
  gap or a data outage, **not** the normal way out. The signal tier is the
  primary expected exit.
- The `score` field is **informational only** — it never gates or ranks anything
  (the boolean chain already decided pass/fail per symbol). It only feeds
  conviction display, which has zero effect on paper exposure (qty=1 always).
- **OPEN session only:** `_entry_watch_loop` runs when `session() == "OPEN"` and
  `entry_allowed()`. Ticks every `ENTRY_WATCH_INTERVAL_S` (30s); pool refreshes
  every `POOL_REFRESH_S` (60s).
- **Alpaca PAPER fills** (`PAPER_TRADING=1`, off): routes booked picks to the
  paper account; the broker's fill (`broker_fill_price`) becomes the ledger entry.
  Fail-closed; reconcile loop in `main.py`.

## Architecture

```
ingest/earnings.py   Nasdaq calendar → candidate pool, UNFILTERED by reaction
                     (-3 to +5 days around the report; judgment lives in desk/watcher.py)
ingest/prices.py     price context (Alpaca live + yfinance), intraday RSI-9, options IV, sector ETFs, macro
quant/signals.py     6 weighted signals → composite −100..+100 (OFFLINE ONLY: desk CLI,
                     backtest, grader, dashboard — NOT the live entry path)
quant/calibrate.py   online + batch weight learning from graded outcomes
quant/watcher.py     tiered exits (TP/trail/stop/spike/stale/RSI-reversal/session-close)
quant/stream.py      Alpaca WebSocket live prices (SPY registered)
desk/watcher.py      LIVE ENTRY ENGINE: continuous per-candidate RSI-only gate → book → funnel
desk/workflow.py     research_run() batch twin (desk CLI, offline)
desk/plan.py         ATR-based entry/target/stop + exit physics
desk/portfolio.py    opt-in Alpaca paper order routing + reconcile
ledger/store.py      SQLite/WAL ledger + funnel/skips + price_daily cache
ledger/grader.py     forward grading vs SPY (alpha_net/alpha_adj), MFE/MAE, reaction A/B
ledger/backtest.py   replay history: does drift pay? (uses the local price cache)
app/dashboard.py     FastAPI: /api/* + SPA
app/alerts.py        webhook notifications (ALERTS_WEBHOOK_URL)
main.py              CLI + 6 async loops (grader, earnings+arm, position watch,
                     entry watch, quant watch, daily summary)
ui/                  React 19 + TS + Vite → app/static/ (router pages: /live /history /pre /open /after /earnings /performance /system)
```

- `desk/stream.py` (the old batch "Find Trades" scanner) was **removed**
  2026-08-13 — `desk/watcher.py` replaced it. Bookings still write a
  `FIND_TRADES` run row so the dashboard's existing `api_system()` query keeps
  working unmodified; pick-level `trigger_src="ENTRY_WATCH"` is what actually
  distinguishes them in the ledger.
- Coverage funnel: every tick logs candidates→picked→why-dropped to `funnel`, and
  drops become day-deduped `skips` (graded forward for missed moves).

## Environment variables (key)

```ini
ALPACA_API_KEY / ALPACA_SECRET_KEY   # market data + universe + paper orders
ALPHADESK_DATA=~/.alphadesk          # ledger.db, universe.json, quant_weights.json

# entry engine (desk/watcher.py) — the live path
RSI_CROSS_OVERSOLD=30                # cross UP through this = LONG
RSI_CROSS_OVERBOUGHT=70              # cross DOWN through this = SHORT
MA_ENTRY_MIN_RVOL=1.2 / MA_ENTRY_MIN_ATR_PCT=1.5
MA_INTRADAY_HISTORY_DAYS=5 / MA_INTRADAY_BAR_MINUTES=1
MA_STOP_BACKSTOP_ATR=4.0             # WIDE backstop, not the primary exit
ENTRY_WATCH_INTERVAL_S=30 / POOL_REFRESH_S=60
MAX_ENTRIES_PER_DAY=100 / MAX_BOOKINGS_PER_SYMBOL_PER_DAY=2 / DAILY_LOSS_STOP_PCT=10
START_BUFFER_MIN=15 / ENTRY_BUFFER_MIN=60 / EXIT_BUFFER_MIN=15

PAPER_TRADING=0 / PM_BASE_USD=1000 / PM_MAX_POSITION_USD=2500 / PM_MAX_POSITIONS=20
ALERTS_WEBHOOK_URL=                  # Telegram/Slack/Discord webhook for pick/exit/risk alerts
SHORT_BORROW_APR / SHORT_BORROW_APR_ILLIQUID   # honest-alpha borrow charge

# offline research tools only (backtest/abtest) — NOT the live entry path
MATERIAL_REACTION_PCT=1.5
```

**Orphaned in `config.py`** (defined, no consumers — don't assume they do
anything): `AUTORUN_INTERVAL_MINUTES` / `AUTORUN_START_ET` / `AUTORUN_END_ET`
(autorun was replaced by the entry-watch loop), `QUANT_PREFILTER_MIN_SCORE`,
`QUANT_SCORE_CANDIDATES`.

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

## Honest status (2026-08-15)

- The live strategy is now **pure RSI-9 mean reversion, OPEN session only** — no
  longer post-earnings drift. The earnings calendar is only a CANDIDATE SOURCE
  (which symbols to watch); the reaction gate no longer selects anything.
- **This engine has no validated edge either — it has essentially no live sample
  yet.** It is days old (MACD+RSI `7fe768a` → RSI-only `68f66d5`, 2026-08-15) and
  its parameters (30/70 thresholds, RSI-9, the rvol/ATR floors, 4.0× backstop)
  are unvalidated first-pass values, not calibrated against outcomes. The
  findings below are about the OLD drift engine and do NOT transfer.
- Nothing below this line has been re-tested against the RSI engine.

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
