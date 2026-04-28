# Institutional-Grade Automated Swing Trading Platform

A quantitative, serverless algorithmic trading platform built in Python. It operates autonomously in the cloud using a three-layer decision stack — Screen → Predict → Execute — powered by AWS Bedrock NLP, adaptive percentile thresholds, and Alpaca brokerage integration. A FastAPI web dashboard provides real-time performance tracking, trade history, and live configuration.

---

## Decision Pipeline

### Layer 1: Screener

`stock_sentiment/market/screener.py` filters a curated universe of **148 equities** down to the top 40 candidates each cycle using a two-pass adaptive system.

**Hard gates (applied first):**

- Relative Volume (RVOL) ≥ 1.0 — daily volume must exceed its 20-day average
- Earnings blackout — rejects any stock reporting earnings within ≤ 3 days
- Minimum 20 trading days of price history required

**Two-pass adaptive thresholds:**

Pass 1 collects raw metrics across the live universe. Pass 2 classifies archetypes against live-universe percentiles — avoiding static cutoffs that get gamed by market conditions.

**Archetypes (a stock must match one):**

| Archetype | Criteria |
| --- | --- |
| **Breakout Star** | 1-week return ≥ 75th percentile OR 1-month return ≥ 75th percentile |
| **Recovery Phoenix** | Drawdown ≤ 30th percentile AND bounce ≥ 65th percentile AND RVOL > 1.1 |
| **Momentum King** | 3-month return ≥ 60th percentile |

The top 40 stocks (ranked by RVOL and 1-week performance) advance to the Predictor.

---

### Layer 2: Predictor

`stock_sentiment/market/stock_predictor.py` generates a conviction score (0–100) per stock.

**Score formula:**

```
final_score = (formula_score × 0.70) + (llm_qualitative × 0.30)
```

- **70% quantitative:** `momentum × w[0] + volume × w[1] + technical × w[2] + sentiment × w[3]`
  Weights are **per-archetype and learned** from backtest outcomes via the Weight Optimizer — not hardcoded.
- **30% qualitative:** A single Claude Haiku batch call scores all 40 stocks on news quality, catalyst strength, and risk narrative.

**Archetype-aware sub-scores:**

| Sub-score | MOMENTUM | BREAKOUT | RECOVERY |
| --- | --- | --- | --- |
| Momentum | `3m_change × 1.5` (max 100) | `1w × 4 + 1m` (max 100) | `60 + 1w × 5` (max 100) |
| Technical (RSI) | 70 if RSI < 70, else 40 | 90 if RVOL > 2.0, else 60 | 95 if RSI < 35; 80 if < 45; else 50 |
| Volume | `50 + min(50, (RVOL - 1.0) × 30)` — same for all archetypes | | |
| Sentiment | `(avg_sentiment + 1) × 50 + 15 bonus if ≥ 3 bullish headlines` | | |

**Thresholds:**

- **BULLISH:** score ≥ regime-adjusted threshold (55–70, see Market Regime)
- **BEARISH:** score ≤ 40
- **NEUTRAL:** between thresholds
- **Red-flag override:** if Claude Haiku returns `red_flag=true`, score is hard-capped at 35 regardless of formula output

---

### Layer 3: Broker

`stock_sentiment/market/broker.py` executes trades via the Alpaca API.

**Position sizing:**

- Max **10 simultaneous positions** (max 8 short, minimum 2 long slots preserved)
- Each slot = **9% of current portfolio value** (minimum $50)
- Whole shares only (fractional notionals are incompatible with trailing stop orders)

**Risk management:**

- Every new position receives a **3.0% GTC trailing stop** immediately on entry
- Stops tighten automatically on profit tiers:
  - Gain ≥ 15% → trailing stop tightens to **1.5%**
  - Gain ≥ 30% → trailing stop tightens to **0.8%**
- DAY-trade positions and **all short positions close at 3:45 PM ET** regardless of conviction
- BEARISH downgrade → immediate liquidation of position + cancellation of stops
- Earnings ≤ 3 days away → pre-emptive close
- **1-hour re-entry cooldown** after a stop-out (tracked in `~/.stock_screener/cooldowns.json`)

**Smart Conviction Swap:** When the portfolio is full and a new BULLISH pick emerges, it swaps the weakest same-direction holding if the new score exceeds the weakest by **> 5 points**.

**Paper vs. live mode:**

- Default: paper trading (`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`)
- Set `ALPACA_PAPER=false` to activate live trading (`ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY`)

---

## Market Regime

`stock_sentiment/market/market_regime.py` fetches SPY and ^VIX each cycle and adjusts buy thresholds and position sizing:

| Regime | Condition | Buy Threshold | Position Sizing |
| --- | --- | --- | --- |
| **HIGH_VOL** | VIX > 30 | 70 | 70% of slot |
| **BEAR** | SPY < 200-day SMA | 65 | 85% of slot |
| **BULL** | SPY > 3% above SMA AND VIX < 20 | 55 | 100% of slot |
| **NEUTRAL** | Everything else | 60 | 100% of slot |

---

## NLP / Bedrock Stack

`stock_sentiment/nlp/sentiment.py` runs all NLP on **AWS Bedrock** — no local model files.

**Article sentiment (bulk scoring):** Model fallback chain tried in order:

1. **Amazon Nova Micro** (`amazon.nova-micro-v1:0`) — primary (cheapest)
2. **Amazon Nova Lite** (`amazon.nova-lite-v1:0`) — fallback
3. **Claude Haiku 4.5** (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) — final fallback

Batch size: 50 articles per call. Output: normalized score from -1.0 (bearish) to +1.0 (bullish).

**Recency decay:** Article weight halves every 48 hours, so stale news contributes less to the sentiment sub-score.

**Source quality tiers:** Reuters/Bloomberg articles are weighted 1.5× vs. generic sources.

**Qualitative conviction (predictor LLM blend):** Claude Haiku (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) receives all 40 screened stocks in a single batch call and returns per-stock scores plus a `red_flag` boolean.

---

## News Providers

`stock_sentiment/market/news_providers.py` abstracts the real-time news feed. Provider is selected in the Settings tab of the dashboard and stored in `~/.stock_screener/settings.json`.

| Provider | Type | Cost | Notes |
| --- | --- | --- | --- |
| **Google News RSS** | Polling (60s cycle) | Free | Default; no API key required |
| **Polygon** | REST polling (60s) | Paid | Requires Polygon API key |
| **Alpaca** | WebSocket stream | Paid subscription | Real-time; requires Alpaca paid data tier |

Provider changes take effect on the next scheduler cycle without restart.

---

## Weight Optimizer

`stock_sentiment/market/weight_optimizer.py` learns optimal `[momentum, volume, technical, sentiment]` weights per archetype from backtest history using **Nelder-Mead optimization** (falls back to random search if scipy is unavailable).

- Requires ≥ 50 global scored outcomes and ≥ 20 per archetype to update weights
- Weights are persisted to `~/.stock_screener/weights.json`
- Run manually with `python run.py --optimize`

---

## Web Dashboard

`web.py` (FastAPI) serves a four-tab dashboard:

| Tab | Content |
| --- | --- |
| **Performance** | Alpaca portfolio equity curve, P&L, positions |
| **Trade History** | Past executed trades with entry/exit prices |
| **Screener** | Latest run results with conviction scores per stock |
| **Settings** | News provider selection, Polygon API key |

Auth: `ADMIN_USERNAME` / `ADMIN_PASSWORD` environment variables (session token-based, password never stored).

---

## Storage

`stock_sentiment/history.py` abstracts the persistence backend:

| Mode | Backend | Location |
| --- | --- | --- |
| Local dev | SQLite | `~/.stock_screener/local_history.db` |
| Production (`ENV=PROD`) | DynamoDB | `PROD_StockScreenerRuns`, `PROD_StockScreenerPredictions`, `PROD_StockScreenerStatus` |

Runtime state files in `~/.stock_screener/`: `cooldowns.json`, `held_cache.json`, `weights.json`, `settings.json`, `last_execution.json`.

---

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run screener once
python run.py

# Run autonomous scheduler (every 15 min by default)
python run.py --schedule

# Run with custom interval (e.g., hourly)
python run.py --schedule --every 1

# Backtest predictions (evaluates ones 5+ days old)
python run.py --backtest

# Show recent alerts
python run.py --alerts

# Optimize per-archetype scoring weights from backtest history
python run.py --optimize

# Start web dashboard
uvicorn web:app --reload --port 8000

# Deploy to AWS (builds Docker image, pushes to ECR, updates ECS)
aws sso login --profile vignesh-sso-profile
./run_aws_bot.sh

# Tail live ECS logs
aws logs tail /ecs/stock-screener --region us-east-1 --follow --profile vignesh-sso-profile
```

---

## Environment Variables

```ini
# Required (paper trading)
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...

# Live trading (set ALPACA_PAPER=false to activate)
ALPACA_PAPER=false
ALPACA_LIVE_API_KEY=...
ALPACA_LIVE_SECRET_KEY=...

# AWS (local dev via SSO profile)
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

---

## Cloud Infrastructure

Deployed to **Amazon ECS Fargate** via `deploy/deploy.sh`:

- **Compute:** 1 vCPU, 4GB RAM (NLP runs on Bedrock — no large model files in the image)
- **Networking:** Application Load Balancer → Fargate container; security groups restrict inbound to ALB only
- **Docker target:** `linux/amd64` — specify platform when building locally on Apple Silicon
- **DynamoDB:** Three tables with `PROD_` prefix, auto-provisioned on first production run
- **S3:** HTML execution snapshots archived by date prefix (`/YYYY/MM/DD/`)
- **SES:** Trade execution and downgrade alerts to verified email identities

---

## Disclaimer

This tool is for educational and informational purposes only. It does not constitute financial advice. Algorithmic trading involves significant risk of loss. Always use paper trading mode for validation before committing real capital.
