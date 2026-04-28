# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run screener once
python run.py

# Run autonomous scheduler (every 15 mins by default)
python run.py --schedule

# Run with custom interval (e.g., hourly)
python run.py --schedule --every 1

# Backtest predictions (checks ones 5+ days old)
python run.py --backtest

# Show recent alerts
python run.py --alerts

# Optimize per-archetype scoring weights from backtest history
python run.py --optimize

# Start web dashboard
uvicorn web:app --reload --port 8000
```

### Cloud Deployment (AWS)
```bash
aws sso login --profile vignesh-sso-profile
./run_aws_bot.sh            # builds Docker, pushes to ECR, updates ECS

# Tail live logs
aws logs tail /ecs/stock-screener --region us-east-1 --follow --profile vignesh-sso-profile
```

## Architecture

This is an algorithmic swing trading bot built on a **three-layer decision stack**: Screen → Predict → Execute.

### Decision Pipeline

1. **Screener** (`stock_sentiment/market/screener.py`) — Filters 148 curated equities down to top 40:
   - Hard gates: RVOL ≥ 1.0, no earnings within 3 days, ≥20 days of price history
   - Two-pass adaptive thresholds: Pass 1 collects raw metrics, Pass 2 classifies archetypes against live-universe percentiles — avoids static cutoffs being gamed by market conditions
   - Archetype classification (a stock must match one):
     - **Breakout Star**: 1-week return ≥ 75th percentile OR 1-month return ≥ 75th percentile
     - **Recovery Phoenix**: drawdown ≤ 30th percentile AND bounce ≥ 65th percentile AND RVOL > 1.1
     - **Momentum King**: 3-month return ≥ 60th percentile

2. **Predictor** (`stock_sentiment/market/stock_predictor.py`) — Generates 0–100 conviction scores:
   - Formula blend (70%): momentum, volume, technicals, sentiment sub-scores with per-archetype learned weights from `weight_optimizer.py`
   - LLM blend (30%): single Claude Haiku batch call scores all 40 stocks qualitatively (news quality, catalyst, risk narrative)
   - Article sentiment uses Amazon Nova Micro (`amazon.nova-micro-v1:0`) as primary, with Nova Lite then Haiku as fallbacks; recency decay halves score weight every 48h; source quality tiers (Reuters/Bloomberg 1.5×, etc.)
   - Red-flag override: Haiku `red_flag=true` hard-caps final score at 35 (forces BEARISH)
   - BULLISH threshold: ≥ 60 | BEARISH: ≤ 40

3. **Broker** (`stock_sentiment/market/broker.py`) — Executes via Alpaca API:
   - Max 10 positions (max 8 short, minimum 2 long slots); 9% of portfolio per position (min $50), whole shares only
   - Market orders with 3.0% trailing GTC stops; stops tighten to 1.5% at +15% gain, 0.8% at +30%
   - DAY trades and all short positions close at 3:45 PM ET
   - Smart Conviction Swapping: new pick with score >5 points above weakest same-direction holding triggers a swap
   - BEARISH downgrade → immediate liquidation; earnings ≤3 days away → pre-emptive close
   - 1-hour re-entry cooldown after stop-out (`~/.stock_screener/cooldowns.json`)
   - Paper mode is default; set `ALPACA_PAPER=false` to switch to live (uses `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY`)

**Orchestrator:** `stock_sentiment/screener_app.py` wires the pipeline; `stock_sentiment/scheduler.py` runs it every 15 min by default (0.25h), respecting Alpaca market clock.

### Weight Optimizer (`stock_sentiment/market/weight_optimizer.py`)

Learns optimal `[momentum, volume, technical, sentiment]` weights per archetype using Nelder-Mead (falls back to random search if scipy unavailable). Requires ≥50 global outcomes, ≥20 per archetype. Weights persisted to `~/.stock_screener/weights.json`.

### News Providers (`stock_sentiment/market/news_providers.py`)

Provider abstraction for real-time article feeds. Selected in the dashboard Settings tab, stored in `~/.stock_screener/settings.json`. Takes effect on next cycle without restart.

- **RSS** (default): Google News RSS, no API key required, 60s polling cycle
- **Polygon** (`PolygonNewsProvider`): polls REST API every 60s, requires Polygon API key
- **Alpaca** (`AlpacaNewsProvider`): WebSocket stream, requires paid Alpaca subscription

### Storage (Dual-Backend Pattern)

`stock_sentiment/history.py` abstracts local vs. cloud persistence:
- **Local dev**: SQLite at `~/.stock_screener/local_history.db`
- **Production** (`ENV=PROD`): DynamoDB tables `PROD_StockScreenerRuns`, `PROD_StockScreenerPredictions`, `PROD_StockScreenerStatus`

### Web Dashboard

`web.py` (FastAPI) + `templates/index.html` + `static/app.js` — four tabs: Performance, Trade History, Screener, Settings. Auth: `ADMIN_USERNAME` / `ADMIN_PASSWORD`. Settings tab exposes news provider selection and Polygon API key (stored masked, never returned by API).

### Cloud Infrastructure

ECS Fargate (1 vCPU, 4GB) behind an ALB. NLP runs entirely on AWS Bedrock — no large model files in the Docker image. Docker target is `linux/amd64`. Deployment fully scripted in `deploy/deploy.sh`.

## Environment Variables

```ini
# Required (paper trading)
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...

# Live trading (set ALPACA_PAPER=false to activate)
ALPACA_PAPER=false
ALPACA_LIVE_API_KEY=...
ALPACA_LIVE_SECRET_KEY=...

# AWS (local dev — SSO profile)
AWS_PROFILE=vignesh-sso-profile
AWS_REGION=us-east-1

# Web dashboard auth
ADMIN_USERNAME=admin
ADMIN_PASSWORD=...

# Production mode (switches SQLite → DynamoDB)
ENV=PROD

# Optional cloud features
S3_BUCKET=...
SES_FROM_EMAIL=...
SES_TO_EMAIL=...
```

## Key Design Notes

- **All NLP runs on AWS Bedrock**: Nova Micro (primary) → Nova Lite → Haiku fallback chain for bulk article scoring; Haiku for qualitative conviction. No local model files — Bedrock credentials via `AWS_PROFILE` or instance role.
- **Archetype matters for scoring**: `MOMENTUM`, `BREAKOUT`, and `RECOVERY` archetypes use different RSI/momentum weightings — always check archetype context when modifying `stock_predictor.py`. Weights are learned per-archetype and stored in `~/.stock_screener/weights.json`.
- **Archetype thresholds are adaptive**: computed from live-universe percentiles each cycle, not static values — do not hardcode percentage comparisons when modifying the screener.
- **Price data is cached 600s** in `price_fetcher.py` to avoid yfinance rate limits.
- **Alerts detect state changes** by diffing consecutive runs in `alerts.py` — they require at least two historical runs to be meaningful.
- **Runtime state files** live in `~/.stock_screener/`: `cooldowns.json` (stop-out re-entry blocks), `held_cache.json` (previous cycle holdings for stop-out detection), `weights.json` (learned scoring weights), `settings.json` (news provider config), `last_execution.json` (most recent trade execution log).
