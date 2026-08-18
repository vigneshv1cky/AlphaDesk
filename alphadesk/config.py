"""AlphaDesk configuration — pure quant. No LLM config needed."""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("alphadesk.config")

ET = ZoneInfo("America/New_York")
DATA_DIR = Path(os.environ.get("ALPHADESK_DATA", "~/.alphadesk")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Trading parameters ──────────────────────────────────────────────────────
FRICTION_BPS_PER_SIDE = 15
LOW_LIQUIDITY_DOLLAR_VOL = 10_000_000
SHORT_BORROW_APR = float(os.environ.get("SHORT_BORROW_APR", "2.0"))
SHORT_BORROW_APR_ILLIQUID = float(os.environ.get("SHORT_BORROW_APR_ILLIQUID", "30.0"))

EDGE_HORIZON_DAYS = {
    "MOMENTUM": int(os.environ.get("EDGE_HORIZON_MOMENTUM", "1")),
    "SPILLOVER": int(os.environ.get("EDGE_HORIZON_SPILLOVER", "1")),
    "THEME": int(os.environ.get("EDGE_HORIZON_THEME", "1")),
    "WORLD": int(os.environ.get("EDGE_HORIZON_WORLD", "1")),
}
DEFAULT_EDGE_HORIZON_DAYS = int(os.environ.get("DEFAULT_EDGE_HORIZON_DAYS", "1"))

ENTRY_GAP_SKIP_PCT = float(os.environ.get("ENTRY_GAP_SKIP_PCT", "2.0"))
MIN_RISK_REWARD_RATIO = float(os.environ.get("MIN_RISK_REWARD_RATIO", "1.5"))
MIN_STOP_DISTANCE_PCT = float(os.environ.get("MIN_STOP_DISTANCE_PCT", "2.0"))
LIMIT_FILL_BUFFER_PCT = float(os.environ.get("LIMIT_FILL_BUFFER_PCT", "0.25"))
LIMIT_FILL_MIN_CUSHION_FRAC = float(os.environ.get("LIMIT_FILL_MIN_CUSHION_FRAC", "0.4"))

SYMBOL_REPICK_COOLDOWN_MIN = 15
WATCH_INTERVAL_S = int(os.environ.get("WATCH_INTERVAL_S", "60"))

# ── Risk rails (paper-desk circuit breakers) ─────────────────────────────────
DAILY_LOSS_STOP_PCT = float(os.environ.get("DAILY_LOSS_STOP_PCT", "10"))
# stop opening new positions after realized (equal-weight) losses pass this today

# ── Earnings ────────────────────────────────────────────────────────────────
# Candidate window for the terminal's watchlist — which symbols the earnings
# calendar surfaces for a human to look at:
# one continuous window from EARNINGS_PRE_WINDOW_DAYS before the report
# through EARNINGS_POST_MAX_DAYS after it (i.e. -3 to +5 days around the
# report date) — no gap, no exclusion window.
EARNINGS_PRE_WINDOW_DAYS = int(os.environ.get("EARNINGS_PRE_WINDOW_DAYS", "3"))
EARNINGS_POST_MAX_DAYS = int(os.environ.get("EARNINGS_POST_MAX_DAYS", "5"))
# MATERIAL_REACTION_PCT no longer gates live candidate sourcing (the entry
# engine judges technical setup, not reaction magnitude) — still used by the
# offline `abtest`/`backtest` research tools to test the old reaction-gate
# hypothesis against history.
MATERIAL_REACTION_PCT = float(os.environ.get("MATERIAL_REACTION_PCT", "1.5"))
REACTION_AB_HORIZON_DAYS = int(os.environ.get("REACTION_AB_HORIZON_DAYS", "3"))

# ── Retired RSI entry engine — BACKTEST PARAMETERS ONLY ──────────────────────
# The autonomous entry engine (desk/watcher.py) was DELETED on 2026-08-16 along
# with every other trading bot: it measured -0.072% mean alpha over 503
# backtested trades and -1.123% over 44 live ones. Nothing in the running
# system reads the constants below — their only consumer is
# ledger/rsi_backtest.py, which replays the retired engine against history.
# Changing them changes a research replay, NOT any live behaviour.
# Positions here are session-scoped (held for hours, not weeks), so the
# direction/entry/exit signal has to move on that same clock. rsi_9 is
# computed on INTRADAY bars (ingest/prices.py's get_intraday_ma_context),
# not daily closes. The indicator period (RSI-9) is hardcoded at the
# computation site, matching the existing ATR-14 precedent — identity of
# the indicator, not a tunable. These are strategy behavior, tunable
# independently of the indicator math.
MA_INTRADAY_BAR_MINUTES = int(os.environ.get("MA_INTRADAY_BAR_MINUTES", "1"))
MA_INTRADAY_HISTORY_DAYS = int(os.environ.get("MA_INTRADAY_HISTORY_DAYS", "5"))
MA_ENTRY_MIN_RVOL = float(os.environ.get("MA_ENTRY_MIN_RVOL", "1.2"))
# Floor only, no ceiling: a stock with near-zero volatility doesn't have room
# to reach a meaningful target/stop even if trend/momentum/volume all confirm
# — the setup is weak regardless. High volatility isn't filtered here since
# plan.atr_plan already scales stop distance to it; an unvalidated
# first-pass value, not yet calibrated against outcomes.
MA_ENTRY_MIN_ATR_PCT = float(os.environ.get("MA_ENTRY_MIN_ATR_PCT", "1.5"))
# RSI crossing thresholds — the entry is a THRESHOLD CROSSING, not "wait
# for the extreme" (that's only knowable in hindsight, after it's already
# reversed). Crossing UP through the oversold line IS the LONG; crossing
# DOWN through the overbought line IS the SHORT — this one cross sets both
# direction and timing, with no separate trend filter voting alongside it.
RSI_CROSS_OVERSOLD = float(os.environ.get("RSI_CROSS_OVERSOLD", "30"))
RSI_CROSS_OVERBOUGHT = float(os.environ.get("RSI_CROSS_OVERBOUGHT", "70"))
# Total bookings (not just "reentries" — there's no separate freshness gate
# anymore) allowed for one symbol+direction per day; a simple backstop
# against rapid oscillation, not a capital control.
MAX_BOOKINGS_PER_SYMBOL_PER_DAY = int(os.environ.get("MAX_BOOKINGS_PER_SYMBOL_PER_DAY", "2"))
# Rarely-triggered backstop, not the primary exit — the RSI reversal
# (quant/watcher.py's trend-reversal tier) is expected to fire first under
# normal conditions. This is deliberately much wider than PLAN_STOP_ATR
# (it is passed explicitly to plan.atr_plan()'s stop_atr_mult override) so it only
# protects against a violent gap or a data outage that leaves the
# signal-based exit unable to compute (which fails open — no exit — so this
# hard floor is the one thing that still catches that case).
MA_STOP_BACKSTOP_ATR = float(os.environ.get("MA_STOP_BACKSTOP_ATR", "4.0"))

# ── Chart data quality (human decision support) ──────────────────────────────
# Alpaca's free IEX feed carries only a few percent of consolidated volume, so
# an illiquid name has no print in most minutes. A "1-minute" RSI/MACD drawn on
# that series is really an N-sample indicator over an unknown time span —
# measured: ENTA had 92 bars across 5 sessions with a 42-min p90 gap, against
# AAPL's 1570 bars at a 1.0-min median. The rendered chart looks identical
# either way, which is the danger: the bot failed quietly, but a misleading
# chart actively recruits a trader's judgment. Below either floor the UI must
# mark indicators unreliable rather than silently drawing them.
CHART_MIN_COVERAGE = float(os.environ.get("CHART_MIN_COVERAGE", "0.5"))        # share of a 390-bar session
CHART_MAX_MEDIAN_GAP_MIN = float(os.environ.get("CHART_MAX_MEDIAN_GAP_MIN", "2.0"))

# How stale the last real Alpaca print may be before a MANUAL booking is
# refused. The entry engine only ever runs during OPEN so it can get away with
# merely checking that last_trade_ts exists; a human can hit the booking
# endpoint at 5pm Sunday, when "last price" is Friday's close — a fill nobody
# could have gotten, which would then be graded as if they had. Freshness also
# catches a halted symbol during an otherwise-open session.
MANUAL_MAX_QUOTE_AGE_S = float(os.environ.get("MANUAL_MAX_QUOTE_AGE_S", "900"))  # 15 min

# ── AI research layer (ingest/news.py, desk/screener.py) ─────────────────────
# The ONLY LLM calls in this repo — reads and compresses news for a human, never
# decides a trade. DeepSeek was already configured here before the v1 multi-agent
# system was removed (11263ae, 2026-08-07); DEEPSEEK_API_KEY / _BASE_URL are
# reused as-is. deepseek-chat, not deepseek-reasoner: this is summarization and
# extraction, not multi-step reasoning, and chat is ~2x cheaper per Mtok.
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "60"))
LLM_MAX_INPUT_CHARS = int(os.environ.get("LLM_MAX_INPUT_CHARS", "24000"))

# Background news loop (main.py's _news_loop). Ingest + enrichment ONLY — it
# no longer pre-generates screener digests. The screener ranks nothing and
# narrates nothing on its own; the AI runs only when a human asks
# (desk/screener.ask), so an idle terminal spends nothing on this page.
NEWS_REFRESH_MINUTES = float(os.environ.get("NEWS_REFRESH_MINUTES", "20"))
NEWS_LOOKBACK_HOURS = float(os.environ.get("NEWS_LOOKBACK_HOURS", "36"))
SCREENER_HORIZON_DAYS = int(os.environ.get("SCREENER_HORIZON_DAYS", "5"))  # upcoming-earnings window
# One ask covers the WHOLE window (every symbol at once), so its input is
# bounded by these two rather than by a top-N cut of the symbol list. The cap
# drops the OLDEST articles first — same policy as ingest/news.py's scan cap.
SCREENER_ASK_MAX_ARTICLES = int(os.environ.get("SCREENER_ASK_MAX_ARTICLES", "120"))
# Per-call input budget, well above LLM_MAX_INPUT_CHARS (24k, sized for one
# symbol's batch of headlines) for the same reason FILING_MAX_CHARS is: this
# call is deliberately wide. A global bump would raise every other call's cost.
SCREENER_ASK_MAX_CHARS = int(os.environ.get("SCREENER_ASK_MAX_CHARS", "40000"))

# ── Filings workspace (ingest/edgar.py, desk/filings.py) ─────────────────────
# A 10-K's meaningful narrative (Business, Risk Factors, MD&A) commonly runs
# 50-100k characters — the news path's LLM_MAX_INPUT_CHARS (24k, sized for a
# batch of short headlines) would truncate before reaching most of it. This is
# a per-call override (ai/deepseek.chat_json's max_input_chars), not a change
# to the global default, so it doesn't raise the cost of every other call site.
FILING_MAX_CHARS = int(os.environ.get("FILING_MAX_CHARS", "60000"))
FILING_RECENT_LIMIT = int(os.environ.get("FILING_RECENT_LIMIT", "12"))

# ── Research agent (desk/research.py) ────────────────────────────────────────
# Q&A over one symbol's pre-fetched fundamentals/ownership/insider/earnings/
# macro/sector data — same "server fetches, one JSON-mode chat_json call
# summarizes-and-cites" shape as desk/filings.py, not a tool-calling loop.
# All 6 sections are wrapped as untrusted <data:*> blocks (ai/deepseek.
# wrap_data), so this needs a much larger input budget than the news path's
# LLM_MAX_INPUT_CHARS (24k, sized for short headlines) — mirrors
# FILING_MAX_CHARS's reasoning exactly.
RESEARCH_MAX_CHARS = int(os.environ.get("RESEARCH_MAX_CHARS", "30000"))
# Unlike symbol_digests/filing_qa_cache, the underlying data (a live quote,
# recent insider filings) can go stale between identical asks even though the
# question text hasn't changed — so this cache needs an actual TTL, not just
# a hash key.
RESEARCH_CACHE_TTL_HOURS = float(os.environ.get("RESEARCH_CACHE_TTL_HOURS", "4"))
# 13F is quarterly, Form 4 is event-driven — both move far slower than
# options IV or a live quote, hence the much longer TTL than prices.py's
# other in-memory caches.
OWNERSHIP_TTL_S = int(os.environ.get("OWNERSHIP_TTL_S", str(6 * 3600)))

# ── Quant ────────────────────────────────────────────────────────────────────
QUANT_STREAM_ENABLED = os.environ.get("QUANT_STREAM_ENABLED", "1") not in ("0", "", "false", "False", "no")
QUANT_TIERED_EXITS = os.environ.get("QUANT_TIERED_EXITS", "1") not in ("0", "", "false", "False", "no")
QUANT_CALIBRATE = os.environ.get("QUANT_CALIBRATE", "1") not in ("0", "", "false", "False", "no")

# Absolute reference point for conviction sizing (composite score that counts as
# "full conviction"). The old batch scanner scaled against the strongest score
# in that run's top-N; with no batch there's nothing to scale against, so this
# is a fixed empirical anchor instead. Doesn't affect order size (qty=1 either
# way) — only the stored conviction/confidence display fields.
# Runaway guard: an uncapped continuous watcher can in principle book far more
# per day than the old top-6-per-cycle scanner ever could. Not a capital/sizing
# control (that's still deferred) — a basic backstop against a bug causing
# repeated/runaway booking, same spirit as the old (now-removed) daily API cap.
MAX_ENTRIES_PER_DAY = int(os.environ.get("MAX_ENTRIES_PER_DAY", "100"))

# ── Exit parameters (self-optimizing via calibrator) ─────────────────────────
PLAN_TARGET_ATR = float(os.environ.get("PLAN_TARGET_ATR", "2.0"))        # target = entry ± ATR × this
PLAN_STOP_ATR = float(os.environ.get("PLAN_STOP_ATR", "0.5"))           # stop = entry ∓ ATR × this
TRAIL_OFFSET_ATR = float(os.environ.get("TRAIL_OFFSET_ATR", "0.15"))    # trail offset = ATR × this
TRAIL_OFFSET_MAX = float(os.environ.get("TRAIL_OFFSET_MAX", "0.02"))    # trail offset ceiling
TRAIL_ACTIVATION = float(os.environ.get("TRAIL_ACTIVATION", "1.5"))     # % profit to activate trail
GIVEBACK_RETAIN = float(os.environ.get("GIVEBACK_RETAIN", "0.4"))       # retain this fraction of peak profit
GIVEBACK_FLOOR = float(os.environ.get("GIVEBACK_FLOOR", "1.0"))         # never exit below this %

# ── Skip grading (anti-survivorship) ────────────────────────────────────────
SKIP_GRADE_DAYS = 3
SKIP_MISS_ABS_ALPHA = 6.0

# ── Lean mode ────────────────────────────────────────────────────────────────
LEAN_MODE = os.environ.get("LEAN_MODE", "1") not in ("0", "", "false", "False", "no")
LEAN_EARNINGS_SKIP_NEWS = int(os.environ.get("LEAN_EARNINGS_SKIP_NEWS", "5"))

# ── Autorun ──────────────────────────────────────────────────────────────────

# ── Paper trading (opt-in) ──────────────────────────────────────────────────
PAPER_TRADING = os.environ.get("PAPER_TRADING", "0") not in ("0", "", "false", "False", "no")
TRADE_NOTIONAL_USD = float(os.environ.get("TRADE_NOTIONAL_USD", "10"))   # $10 fractional per trade
PM_BASE_USD = float(os.environ.get("PM_BASE_USD", "1000"))
PM_MAX_POSITION_USD = float(os.environ.get("PM_MAX_POSITION_USD", "2500"))
PM_MAX_POSITIONS = int(os.environ.get("PM_MAX_POSITIONS", "20"))
PM_EXTENDED_HOURS = os.environ.get("PM_EXTENDED_HOURS", "0") not in ("0", "", "false", "False", "no")


def pinned_horizon(edge: str | None) -> int:
    return EDGE_HORIZON_DAYS.get((edge or "").upper(), DEFAULT_EDGE_HORIZON_DAYS)


# ── Market sessions ──────────────────────────────────────────────────────────

def now_et() -> datetime:
    return datetime.now(ET)


def session(dt: datetime | None = None) -> str:
    dt = (dt or now_et()).astimezone(ET)
    if dt.weekday() >= 5:
        return "CLOSED"
    minutes = dt.hour * 60 + dt.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "PRE"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "OPEN"
    if 16 * 60 <= minutes < 20 * 60:
        return "AFTER"
    return "CLOSED"


def next_market_open(dt: datetime) -> datetime:
    dt = dt.astimezone(ET)
    def open_at(d) -> datetime:
        return datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    if dt.weekday() < 5 and dt < open_at(dt.date()):
        return open_at(dt.date())
    d = dt.date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return open_at(d)


def next_session_open(dt: datetime) -> datetime:
    """ET moment the next TRADEABLE session opens — the PRE (4:00) of the next
    trading day. Session-scoped model (no carry-over across markets): a pick
    decided when the market is closed enters at this moment and exits at that
    session's close. If we're in a weekday night BEFORE 4:00, that's today's
    PRE; otherwise it's the next trading day's 4:00 PRE."""
    dt = dt.astimezone(ET)
    def pre_at(d) -> datetime:
        return datetime(d.year, d.month, d.day, 4, 0, tzinfo=ET)
    if dt.weekday() < 5 and dt < pre_at(dt.date()):
        return pre_at(dt.date())
    d = dt.date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return pre_at(d)


def market_context_line() -> str:
    now = now_et()
    sess = session(now)
    nxt = next_market_open(now)
    if sess == "OPEN":
        return f"Market: OPEN ({now:%a %Y-%m-%d %H:%M ET}). Fills immediately."
    return (f"Market: {sess} ({now:%a %Y-%m-%d %H:%M ET}). "
            f"Fills at next open ({nxt:%a %Y-%m-%d 09:30 ET}).")


def entry_fill_time(ts_iso: str, sess: str | None) -> datetime | None:
    """When a pick's entry actually fills, in ET.
    Session-scoped model: PRE/OPEN/AFTER picks fill LIVE at decision time
    (regular or extended hours); a CLOSED/night pick (no market open) enters at
    the next session's open (4:00 PRE) and exits at that session's close."""
    try:
        dt = datetime.fromisoformat(ts_iso).astimezone(ET)
    except (ValueError, TypeError):
        return None
    if sess in ("OPEN", "PRE", "AFTER"):
        return dt
    return next_session_open(dt)


# ── Per-market entry/exit buffers ─────────────────────────────────────────────
# Each tradeable window is its own trade. Trade only the SETTLED middle of each
# session — the open and the run-up to the close are the worst fills (wide
# spreads, thin books, end-of-session imbalance):
#   • START buffer — no NEW entries until START_BUFFER_MIN after the session opens.
#   • EXIT buffer  — every position exits EXIT_BUFFER_MIN before the session close.
#   • ENTRY buffer — no NEW positions in the last ENTRY_BUFFER_MIN of a session,
#     so we never buy when we're about to close (the last entry still gets a real
#     hold before the exit).
# Night (CLOSED) is exempt on all three — nothing trades 20:00–4:00; night-decided
# picks queue for the next 4:00 open and get a full window.
EXIT_BUFFER_MIN = int(os.environ.get("EXIT_BUFFER_MIN", "15"))
ENTRY_BUFFER_MIN = int(os.environ.get("ENTRY_BUFFER_MIN", "60"))
START_BUFFER_MIN = int(os.environ.get("START_BUFFER_MIN", "15"))

SESSION_OPEN_MIN = {"PRE": 4 * 60, "OPEN": 9 * 60 + 30, "AFTER": 16 * 60}
SESSION_CLOSE_MIN = {"PRE": 9 * 60 + 30, "OPEN": 16 * 60, "AFTER": 20 * 60}
SESSION_EXIT_MIN = {s: close - EXIT_BUFFER_MIN for s, close in SESSION_CLOSE_MIN.items()}
SESSION_ENTRY_DEADLINE_MIN = {s: close - ENTRY_BUFFER_MIN for s, close in SESSION_CLOSE_MIN.items()}
SESSION_ENTRY_OPEN_MIN = {s: open_min + START_BUFFER_MIN for s, open_min in SESSION_OPEN_MIN.items()}


def entry_allowed(now: datetime | None = None) -> bool:
    """True if a NEW position may be opened right now. False when the current
    session is either still in its START buffer (the open is volatile — thin
    order books, wide spreads) or already inside its END entry buffer (a pick
    opened then would barely have time to work before the session-close exit).
    CLOSED (night) is allowed: those picks queue for the next 4:00 PRE open and
    get a full window."""
    dt = (now or now_et()).astimezone(ET)
    sess = session(dt)
    if sess == "CLOSED":
        return True
    minutes = dt.hour * 60 + dt.minute
    if minutes < SESSION_ENTRY_OPEN_MIN[sess]:
        return False
    return minutes < SESSION_ENTRY_DEADLINE_MIN[sess]


# ── Universe ─────────────────────────────────────────────────────────────────

_UNIVERSE_CACHE = DATA_DIR / "universe.json"
_UNIVERSE_MAX_AGE_S = 7 * 24 * 3600
_universe: set[str] | None = None


def _fetch_universe_from_alpaca() -> list[str]:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest
    client = TradingClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)
    assets = client.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY))
    return sorted({a.symbol for a in assets if not isinstance(a, str) and getattr(a, "tradable", False)})


def load_universe(refresh: bool = False) -> set[str]:
    global _universe
    if _universe is not None and not refresh:
        return _universe
    cache_ok = _UNIVERSE_CACHE.exists() and (time.time() - _UNIVERSE_CACHE.stat().st_mtime < _UNIVERSE_MAX_AGE_S)
    if cache_ok and not refresh:
        _universe = set(json.loads(_UNIVERSE_CACHE.read_text()))
        return _universe
    try:
        symbols = _fetch_universe_from_alpaca()
        _UNIVERSE_CACHE.write_text(json.dumps(symbols))
        _universe = set(symbols)
        log.info("Universe: %d tradable symbols", len(symbols))
    except Exception as exc:
        if _UNIVERSE_CACHE.exists():
            _universe = set(json.loads(_UNIVERSE_CACHE.read_text()))
            log.warning("Universe refresh failed (%s) — stale cache", exc)
        else:
            raise RuntimeError(f"No universe available: {exc}") from exc
    return _universe


def in_universe(symbol: str) -> bool:
    return symbol.upper() in (load_universe() or set())
