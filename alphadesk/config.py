"""AlphaDesk configuration.

Everything here is read from the environment with a working default, so a
fresh checkout runs without a .env. Only three settings have no sensible
default and must be supplied: ALPACA_API_KEY / ALPACA_SECRET_KEY (market data
and the tradable universe) and SEC_USER_AGENT (SEC requires real contact
info — see ingest/edgar.py).

Historical note: this file used to carry ~150 lines of trading parameters
(entry gates, ATR stops, trailing exits, paper-trading sizing, session entry
buffers). Every one of them was orphaned when the execution and measurement
layers were removed on 2026-08-18, and they were deleted on 2026-08-19. Git
history has them if that direction ever returns; a live config file listing
knobs nothing reads is worse than no record at all.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("alphadesk.config")

ET = ZoneInfo("America/New_York")
DATA_DIR = Path(os.environ.get("ALPHADESK_DATA", "~/.alphadesk")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Liquidity ────────────────────────────────────────────────────────────────
# 20-day average dollar volume below which a name is flagged thin. Surfaced as
# evidence on the Earnings page and in price context — never as a filter that
# removes a row, since "too thin to trade" is the reader's call to make.
LOW_LIQUIDITY_DOLLAR_VOL = 10_000_000

# ── Chart reference lines ────────────────────────────────────────────────────
# Drawn on the RSI panel so a reader can see where conventional oversold /
# overbought sit. They are DISPLAY thresholds only: nothing in this codebase
# acts on a crossing. The automated engine that did was deleted on 2026-08-16
# after measuring -0.072% mean alpha over 503 backtested trades.
RSI_CROSS_OVERSOLD = float(os.environ.get("RSI_CROSS_OVERSOLD", "30"))
RSI_CROSS_OVERBOUGHT = float(os.environ.get("RSI_CROSS_OVERBOUGHT", "70"))

# ── Chart data quality (human decision support) ──────────────────────────────
# Alpaca's free IEX feed carries only a few percent of consolidated volume, so
# an illiquid name has no print in most minutes. A "1-minute" RSI/MACD drawn on
# that series is really an N-sample indicator over an unknown time span —
# measured: ENTA had 92 bars across 5 sessions with a 42-min p90 gap, against
# AAPL's 1570 bars at a 1.0-min median. The rendered chart looks identical
# either way, which is the danger: a misleading chart actively recruits a
# reader's judgment. Below either floor the UI must hide the indicators rather
# than silently drawing them.
CHART_MIN_COVERAGE = float(os.environ.get("CHART_MIN_COVERAGE", "0.5"))        # share of a 390-bar session
CHART_MAX_MEDIAN_GAP_MIN = float(os.environ.get("CHART_MAX_MEDIAN_GAP_MIN", "2.0"))

# ── LLM transport (ai/llm.py, providers/llm.py) ──────────────────────────────
# The model itself is selected by LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL (see
# providers/llm.py). DEEPSEEK_* survive as fallbacks so a deployment predating
# the provider seam keeps working without touching its .env.
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "60"))
LLM_MAX_INPUT_CHARS = int(os.environ.get("LLM_MAX_INPUT_CHARS", "24000"))

# ── News ingest (main.py's _news_loop, ingest/news.py) ───────────────────────
# Ingest + enrichment ONLY — the loop does not pre-generate digests. The
# screener ranks nothing and narrates nothing on its own; the AI runs only when
# a human asks (desk/screener.ask), so an idle terminal spends nothing.
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
# a per-call override (ai/llm.chat_json's max_input_chars), not a change to the
# global default, so it doesn't raise the cost of every other call site.
FILING_MAX_CHARS = int(os.environ.get("FILING_MAX_CHARS", "60000"))

# ── Research agent (desk/research.py) ────────────────────────────────────────
# Q&A over one symbol's pre-fetched fundamentals/ownership/insider/earnings/
# macro/sector data — same "server fetches, one chat_json call summarizes-and-
# cites" shape as desk/filings.py, not a tool-calling loop. All 6 sections are
# wrapped as untrusted <data:*> blocks (ai/llm.wrap_data), so this needs a much
# larger input budget than the news path — mirrors FILING_MAX_CHARS's reasoning.
RESEARCH_MAX_CHARS = int(os.environ.get("RESEARCH_MAX_CHARS", "30000"))
# Unlike symbol_digests/filing_qa_cache, the underlying data (a live quote,
# recent insider filings) can go stale between identical asks even though the
# question text hasn't changed — so this cache needs an actual TTL, not just
# a hash key.
RESEARCH_CACHE_TTL_HOURS = float(os.environ.get("RESEARCH_CACHE_TTL_HOURS", "4"))
# 13F is quarterly, Form 4 is event-driven — both move far slower than a live
# quote, hence the much longer TTL than prices.py's other in-memory caches.
OWNERSHIP_TTL_S = int(os.environ.get("OWNERSHIP_TTL_S", str(6 * 3600)))


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


# ── Universe ─────────────────────────────────────────────────────────────────

_UNIVERSE_CACHE = DATA_DIR / "universe.json"
# Company names ride along with the universe refresh: the asset list already
# carries a name for every symbol and this used to throw it away, which is why
# a movers row could only ever show a bare ticker. Separate file so the
# universe cache keeps its existing shape (a plain list, read by in_universe).
_NAMES_CACHE = DATA_DIR / "symbol_names.json"
_UNIVERSE_MAX_AGE_S = 7 * 24 * 3600
_universe: set[str] | None = None
_names: dict[str, str] | None = None


def _fetch_universe_from_alpaca() -> list[str]:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest
    from alphadesk.net import bound_timeout
    client = bound_timeout(TradingClient(
        os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True))
    assets = client.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY))
    tradable = [a for a in assets if not isinstance(a, str) and getattr(a, "tradable", False)]
    try:
        _NAMES_CACHE.write_text(json.dumps(
            {a.symbol: (getattr(a, "name", "") or "").strip() for a in tradable}))
    except Exception as exc:                     # a missing name file is cosmetic
        log.warning("could not cache symbol names: %s", exc)
    return sorted({a.symbol for a in tradable})


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


def company_name(symbol: str) -> str | None:
    """Display name for a tradable symbol, from the cached asset list.

    Returns None rather than the ticker when unknown — the caller decides
    whether a blank cell or a repeated ticker reads better, and repeating it
    would just be noise beside the symbol column.
    """
    global _names
    if _names is None:
        try:
            _names = json.loads(_NAMES_CACHE.read_text())
        except Exception:
            # Populated on the next universe refresh; until then every row
            # simply has no name, which renders as an empty cell.
            _names = {}
    return _names.get(symbol.upper()) or None


# The index/commodity/crypto strip pinned across the top of the terminal.
# Comma-separated yfinance symbols; the label is what the tape shows.
MARKET_TAPE = [
    s.strip() for s in os.environ.get(
        "MARKET_TAPE", "^GSPC:S&P 500,^DJI:Dow 30,^IXIC:Nasdaq,^RUT:Russell 2000,"
                       "^TNX:US 10Y,CL=F:Crude,GC=F:Gold,BTC-USD:Bitcoin"
    ).split(",") if s.strip()
]
