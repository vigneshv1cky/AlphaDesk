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
REPICK_COOLDOWN_HOURS = int(os.environ.get("REPICK_COOLDOWN_HOURS", "24"))
WATCH_INTERVAL_S = int(os.environ.get("WATCH_INTERVAL_S", "60"))

# ── Risk rails (paper-desk circuit breakers) ─────────────────────────────────
MAX_OPEN_POSITIONS = int(os.environ.get("MAX_OPEN_POSITIONS", "20"))
DAILY_LOSS_STOP_PCT = float(os.environ.get("DAILY_LOSS_STOP_PCT", "10"))
# stop opening new positions after realized (equal-weight) losses pass this today
CONCENTRATION_MAX_PER_CLUSTER = int(os.environ.get("CONCENTRATION_MAX_PER_CLUSTER", "2"))
# thin sessions size down (conviction multiplier applied to new picks)
SESSION_SIZE_MULT = {"OPEN": 1.0, "PRE": 0.5, "AFTER": 0.5}

# ── Earnings ────────────────────────────────────────────────────────────────
EARNINGS_DRIFT_DAYS = 3
MATERIAL_REACTION_PCT = float(os.environ.get("MATERIAL_REACTION_PCT", "1.5"))
REACTION_AB_HORIZON_DAYS = int(os.environ.get("REACTION_AB_HORIZON_DAYS", "3"))

# ── Quant ────────────────────────────────────────────────────────────────────
QUANT_STREAM_ENABLED = os.environ.get("QUANT_STREAM_ENABLED", "1") not in ("0", "", "false", "False", "no")
QUANT_PREFILTER_MIN_SCORE = float(os.environ.get("QUANT_PREFILTER_MIN_SCORE", "5.0"))
QUANT_TIERED_EXITS = os.environ.get("QUANT_TIERED_EXITS", "1") not in ("0", "", "false", "False", "no")
QUANT_CALIBRATE = os.environ.get("QUANT_CALIBRATE", "1") not in ("0", "", "false", "False", "no")
# How many candidates each Find Trades run scores (by reaction magnitude). A run
# must finish within the 5-min autorun cadence to catch intraday movers, so keep
# this small — each scored name costs a yfinance price/options/fundamental fetch.
QUANT_SCORE_CANDIDATES = int(os.environ.get("QUANT_SCORE_CANDIDATES", "40"))

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
AUTORUN_INTERVAL_MINUTES = float(os.environ.get("AUTORUN_INTERVAL_MINUTES", "15"))
AUTORUN_START_ET = os.environ.get("AUTORUN_START_ET", "09:35").strip()
AUTORUN_END_ET = os.environ.get("AUTORUN_END_ET", "16:00").strip()

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
# Each tradeable window is its own trade. Give it room to work at both ends AND
# a settled start:
#   • START buffer — the first minutes of a session are the most volatile (wide
#     spreads, unreliable fills at the 9:30 open); no NEW entries until
#     START_BUFFER_MIN after the session opens.
#   • EXIT buffer  — every position exits EXIT_BUFFER_MIN before its session's
#     close, so the close clears before the market flips to the next window.
#   • ENTRY buffer — no NEW positions in the last ENTRY_BUFFER_MIN of a session;
#     a pick opened then would barely have time to work before that exit.
# Night (CLOSED) is exempt on all three — nothing trades 20:00–4:00; night-decided
# picks queue for the next 4:00 open and get a full window.
EXIT_BUFFER_MIN = int(os.environ.get("EXIT_BUFFER_MIN", "5"))
ENTRY_BUFFER_MIN = int(os.environ.get("ENTRY_BUFFER_MIN", "15"))
START_BUFFER_MIN = int(os.environ.get("START_BUFFER_MIN", "5"))

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
