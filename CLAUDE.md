# CLAUDE.md

Guidance for AI coding agents working in this repo. **The docs below describe the
CURRENT pure-quant engine — the old LLM multi-agent architecture was removed in
commit `11263ae` (v2, 2026-08-07). There are zero LLM calls.**

## What this is

**AlphaDesk** — a pure-quant post-earnings-drift research engine. No LLM, no
agents. It watches the Nasdaq earnings calendar, scores reporters with weighted
statistical signals, books session-scoped paper positions, and a self-grading
ledger scores every call forward vs SPY. **Research / paper only — no live order
execution** (Alpaca PAPER fills are opt-in and off by default).

## Commands

```bash
pip install -r requirements.txt

python -m alphadesk.main dashboard      # FastAPI + SPA at :8000, autorun + watchers + grader
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
  15:45 / 19:45 — the last entry still gets ~45 min before the exit.
- **Entry** fills at the live price when found (autorun every 5 min); a pick with
  no live trade is not taken. **Exit** is pure code: hard target/stop, trailing
  stop, spike reversal, stale expiry, and the session-close sweep — all in
  `quant/watcher.py`. `record_exit` stamps `alpha_net = exit alpha` + `graded_at`
  (an exit IS the grade; the forward grader never re-grades a closed session trade).
- **Risk rails** (`config.py`, enforced in `desk/stream.py`): `MAX_OPEN_POSITIONS`
  (20), `CONCENTRATION_MAX_PER_CLUSTER` (2 per sector|direction/day),
  `DAILY_LOSS_STOP_PCT` (10), and 0.5× conviction sizing in PRE/AFTER. Each
  trigger records a funnel/skip reason.
- **Alpaca PAPER fills** (`PAPER_TRADING=1`, off): routes booked picks to the
  paper account; the broker's fill (`broker_fill_price`) becomes the ledger entry.
  Fail-closed; reconcile loop in `main.py`.

## Architecture

```
ingest/earnings.py   Nasdaq calendar → drift candidates (reaction-gated ≥1.5%) + pre-earnings
ingest/prices.py     price context (Alpaca live + yfinance), options IV, sector ETFs, macro
quant/signals.py     6 weighted signals → composite −100..+100 → direction
quant/calibrate.py   online + batch weight learning from graded outcomes
quant/watcher.py     tiered exits (TP/trail/stop/spike/stale/session-close)
quant/stream.py      Alpaca WebSocket live prices (SPY registered)
desk/stream.py       Find Trades pipeline: candidates → risk rails → score → book → funnel
desk/workflow.py     research_run() batch twin (desk CLI)
desk/plan.py         ATR-based entry/target/stop + exit physics
desk/portfolio.py    opt-in Alpaca paper order routing + reconcile
ledger/store.py      SQLite/WAL ledger + funnel/skips + price_daily cache
ledger/grader.py     forward grading vs SPY (alpha_net/alpha_adj), MFE/MAE, reaction A/B
ledger/backtest.py   replay history: does drift pay? (uses the local price cache)
app/dashboard.py     FastAPI: /api/* + SPA
app/alerts.py        webhook notifications (ALERTS_WEBHOOK_URL)
main.py              CLI + 5 async loops (grader, earnings+arm, autorun, watcher, quant watcher)
ui/                  React 19 + TS + Vite → app/static/ (router pages: /live /history /pre /open /after /earnings /performance /system)
```

- Autorun: `AUTORUN_INTERVAL_MINUTES` (5 on VM), every run recorded in `runs`
  (restart-safe interval gate). Runs score ≤ `QUANT_SCORE_CANDIDATES` (40) top
  movers with one batched moves_since_report + 8-way concurrent scoring, so a run
  finishes well inside the cadence.
- Coverage funnel: every run logs candidates→picked→why-dropped to `funnel`, and
  drops become day-deduped `skips` (graded forward for missed moves).

## Environment variables (key)

```ini
ALPACA_API_KEY / ALPACA_SECRET_KEY   # market data + universe + paper orders
ALPHADESK_DATA=~/.alphadesk          # ledger.db, universe.json, quant_weights.json
AUTORUN_INTERVAL_MINUTES=5
AUTORUN_START_ET=04:00 / AUTORUN_END_ET=19:00
MATERIAL_REACTION_PCT=1.5            # earnings-drift gate
QUANT_PREFILTER_MIN_SCORE=5.0        # composite pre-filter
QUANT_SCORE_CANDIDATES=40            # scored per run
MAX_OPEN_POSITIONS=20 / CONCENTRATION_MAX_PER_CLUSTER=2 / DAILY_LOSS_STOP_PCT=10
START_BUFFER_MIN=5 / ENTRY_BUFFER_MIN=15 / EXIT_BUFFER_MIN=5
PAPER_TRADING=0 / PM_BASE_USD=1000 / PM_MAX_POSITION_USD=2500 / PM_MAX_POSITIONS=20
ALERTS_WEBHOOK_URL=                  # Telegram/Slack/Discord webhook for pick/exit/risk alerts
SHORT_BORROW_APR / SHORT_BORROW_APR_ILLIQUID   # honest-alpha borrow charge
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

## Honest status + findings (2026-08-07)

- **The post-earnings-drift edge is NOT validated.** Backtests (1700+ reports,
  raw + composite-selection) and 300+ live exits all land near-zero-to-negative.
  The material-reaction gate doesn't select winners (dropped arm ≈ better).
- **Where a pocket might be:** composite score **5–10**, **OPEN** session,
  **LONG** only (+3% α in the selection backtest). High scores (>20) and PRE/AFTER
  sessions and SHORTs lose consistently. Tuning toward that pocket is the live
  decision on the table — not yet applied; the user is running all sessions to
  build the sample first.
- **Risk rails are live** and the book is capped at 20. Live per-market P&L
  (Performance page): OPEN profitable, PRE/AFTER heavy drags.
- Backtests read a **local daily-price cache** (`price_daily` table) — backfill
  once via `--days N` / first run, then re-runs are ~1 min, not rate-limited.
- Previous session notes (2026-07-27) about the LLM system are obsolete.
