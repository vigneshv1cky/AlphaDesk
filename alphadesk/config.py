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
# Symbol metadata rides along with the universe refresh: the asset list already
# carries a name, an exchange and an asset class for every symbol, and this
# used to throw all three away — which is why a movers row could only show a
# bare ticker. Separate file so the universe cache keeps its existing shape (a
# plain list, read by in_universe).
#
# v2 filename because the shape changed from {symbol: name} to a dict per
# symbol. A stale v1 file is simply ignored rather than mis-parsed; the next
# refresh writes the new one.
_NAMES_CACHE = DATA_DIR / "symbol_meta_v2.json"
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

    def _val(x) -> str:
        # The SDK hands back enums for exchange/class; their .value is the
        # short code a reader recognises ("NASDAQ", "us_equity").
        return str(getattr(x, "value", x) or "").strip()

    try:
        _NAMES_CACHE.write_text(json.dumps({
            a.symbol: {
                "name": (getattr(a, "name", "") or "").strip(),
                "exchange": _val(getattr(a, "exchange", "")),
                "class": _val(getattr(a, "asset_class", "")),
            } for a in tradable}))
    except Exception as exc:                     # a missing meta file is cosmetic
        log.warning("could not cache symbol metadata: %s", exc)
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


_CLASS_LABEL = {"us_equity": "Equity", "crypto": "Cryptocurrency",
                "us_option": "Option", "crypto_perp": "Crypto perpetual"}


def _row(sym: str, meta: dict) -> dict:
    return {"symbol": sym, "name": meta.get("name") or None,
            "exchange": meta.get("exchange") or None,
            "asset_class": _CLASS_LABEL.get(meta.get("class", ""), meta.get("class") or None)}


_WORD_RE = __import__("re").compile(r"[^A-Z0-9.]+")

# Name markers for instruments that merely REFERENCE a company rather than
# being it. "tesla" should offer TSLA before a 2x inverse ETN with Tesla in its
# title, and the asset class cannot tell them apart — Alpaca files all of these
# as us_equity, so the name is the only signal available.
_DERIVATIVE_MARKERS = (
    " ETF", " ETN", "ETNS", "LEVERAGED", "INVERSE", " 2X", " 3X", "-1X",
    "BULL", "BEAR", "INDEX-LINKED", "TRUST SERIES", "COVERED CALL",
)


def _norm(text: str) -> str:
    """Uppercase, punctuation to spaces, whitespace collapsed.

    This is what makes "coca cola" find Coca-Cola. The old search asked whether
    the raw query was a substring of the raw name, so a hyphen in the company
    and a space in the query missed each other completely and KO was simply
    unreachable by name.
    """
    return " ".join(_WORD_RE.sub(" ", (text or "").upper()).split())


def _starts_word(haystack: str, token: str) -> bool:
    """Does `token` begin a word in `haystack`? Both are already normalised, so
    words are simply space-separated."""
    return haystack.startswith(token) or (" " + token) in haystack


def _rank(sym: str, name: str, q: str, q_norm: str, tokens: list[str]) -> int | None:
    """Lower is better; None means no match.

    Tiers rather than a similarity score, because the tiers are the intent: an
    exact ticker is never not what was meant, a ticker prefix is the next most
    likely, and only then does the company name matter.
    """
    name_norm = _norm(name)
    penalty = 50 if any(m in f" {name_norm} " for m in _DERIVATIVE_MARKERS) else 0

    if sym == q:
        return 0
    if sym.startswith(q):
        return 100 + len(sym)
    if name_norm.startswith(q_norm):
        return 200 + penalty + len(sym)
    # The ticker CONTAINS the query. Typing "fd" should reach CLFD and BZFD,
    # not just the forty-one symbols that happen to begin FD — a substring of a
    # ticker is a ticker hunt, and there was no tier for it at all.
    #
    # Below name-prefix on purpose. Two letters match an enormous number of
    # symbols somewhere in the middle, and for a query like "co" the company
    # whose NAME starts with it (Coca-Cola, Costco) is far likelier to be the
    # one meant than an arbitrary ticker with CO buried in it.
    if len(q) >= 2 and q in sym:
        return 250 + len(sym)
    if not name_norm:
        return None
    # Every token present AT A WORD START, in any order — "global venture"
    # finds Venture Global. Word-anchored rather than anywhere-in-the-string
    # because a bare substring makes short queries meaningless: "f" appears
    # inside CLEARFIELD, and matching that returns half the market rather than
    # a search.
    if tokens and all(_starts_word(name_norm, t) for t in tokens):
        return 300 + penalty + len(sym)
    # Loosest tier, and two characters minimum for the same reason.
    if len(q_norm) >= 2 and q_norm in name_norm:
        return 400 + penalty + len(sym)
    return None


def search_symbols(query: str, limit: int = 12) -> list[dict]:
    """Ticker/name search over the cached Alpaca asset list.

    In-memory over ~13k entries, so no index and no round trip to a vendor is
    warranted — a linear scan of that is microseconds and the list only changes
    on the weekly universe refresh.

    Ranked in tiers: exact ticker, ticker prefix, name prefix, all query tokens
    present, then a loose substring. Within a tier a derivative is demoted and
    the shorter ticker wins, which favours the primary listing over the ETFs
    named after it — "jpmorgan" used to answer JIG, a JPMorgan ETF, ahead of
    JPM itself, because both merely contained the word and the tie fell to
    dictionary order.

    Ties break on the symbol, so the same query always returns the same list.
    """
    q = (query or "").strip().upper()
    if not q:
        return []
    _load_names()
    q_norm = _norm(q)
    tokens = [t for t in q_norm.split() if t]

    scored: list[tuple[int, str, str]] = []
    for sym, meta in (_names or {}).items():
        name = (meta or {}).get("name", "") or ""
        r = _rank(sym, name, q, q_norm, tokens)
        if r is not None:
            scored.append((r, sym, name))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [_row(sym, (_names or {}).get(sym) or {}) for _r, sym, _n in scored[:limit]]


def _load_names() -> None:
    global _names
    if _names is None:
        try:
            raw = json.loads(_NAMES_CACHE.read_text())
            # Guard the shape: a v1 file (symbol -> string) would otherwise be
            # read as metadata and every lookup would return nonsense.
            _names = raw if all(isinstance(v, dict) for v in raw.values()) else {}
        except Exception:
            _names = {}


def symbol_meta(symbol: str) -> dict | None:
    """Everything cached about one symbol — name, exchange, asset class."""
    _load_names()
    meta = (_names or {}).get(symbol.upper())
    return _row(symbol.upper(), meta) if meta else None


def company_name(symbol: str) -> str | None:
    """Display name for a tradable symbol, from the cached asset list.

    Returns None rather than the ticker when unknown — the caller decides
    whether a blank cell or a repeated ticker reads better, and repeating it
    would just be noise beside the symbol column.
    """
    # Populated on the next universe refresh; until then every row simply has
    # no name, which renders as an empty cell.
    _load_names()
    return ((_names or {}).get(symbol.upper()) or {}).get("name") or None


# The index/commodity/crypto strip pinned across the top of the terminal.
# Comma-separated yfinance symbols; the label is what the tape shows.
MARKET_TAPE = [
    s.strip() for s in os.environ.get(
        "MARKET_TAPE", "^GSPC:S&P 500,^DJI:Dow 30,^IXIC:Nasdaq,^RUT:Russell 2000,"
                       "^TNX:US 10Y,CL=F:Crude,GC=F:Gold,BTC-USD:Bitcoin"
    ).split(",") if s.strip()
]


# The cross-asset panel, which is deliberately WIDER than the tape above it.
# A panel that lists exactly what the ticker already scrolls past is a second
# copy, not a second view: the strip is five things you glance at, this is the
# board you read. Hence silver and the FX pairs, which the strip has no room
# for. Same "symbol:label" form, same yfinance symbols.
INDEX_BOARD = [
    s.strip() for s in os.environ.get(
        "INDEX_BOARD", "^GSPC:S&P 500,^DJI:Dow 30,^IXIC:Nasdaq,^RUT:Russell 2000,"
                       "^VIX:VIX,^TNX:US 10Y,CL=F:Crude,GC=F:Gold,SI=F:Silver,"
                       "EURUSD=X:EUR/USD,GBPUSD=X:GBP/USD,JPY=X:USD/JPY"
    ).split(",") if s.strip()
]

# The crypto board, on Alpaca rather than yfinance: this is a keyed API, and
# an 18-symbol hourly scrape returned 4 of 18 rows under throttling while the
# same request to Alpaca returned all of them. Stablecoins are left out on
# purpose — USDT/USDC in a movers list is a row that structurally cannot move.
# Ranked by measured 24h change and turnover — which is a
# measurement, not the composite scoring that was deleted on 2026-08-18. That
# was a judgment about which names are INTERESTING; this is the same ordering
# the equity movers list already takes from the provider, computed here only
# because no screener endpoint covers crypto.
# Curated baskets. A theme is a NAME and a list of symbols — nothing is scored,
# ranked or picked here, and the order is the order you write. That keeps it on
# the right side of the 2026-08-18 screener-ranking deletion: choosing what
# belongs in "AI & Tech" is an editorial act, and it is done once, in config,
# where a reader can see it and change it — not computed per-request and
# presented as a finding.
#
# Override wholesale with THEMES_JSON, which must be a JSON list of
# {"id", "label", "symbols"}. Left as a literal rather than a packed string
# because a nested list in a comma-separated env var is unreadable.
_DEFAULT_THEMES = [
    {"id": "mag-7", "label": "Magnificent Seven",
     "symbols": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]},
    {"id": "ai-tech", "label": "AI & Tech",
     "symbols": ["NVDA", "AMD", "AVGO", "PLTR", "MU", "ARM", "TSM", "SMCI"]},
    {"id": "semis", "label": "Semiconductors",
     "symbols": ["NVDA", "AMD", "AVGO", "TSM", "MU", "INTC", "QCOM", "TXN", "ADI"]},
    {"id": "financials", "label": "Financial Services",
     "symbols": ["JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "AXP"]},
    {"id": "energy", "label": "Energy",
     "symbols": ["XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "OXY"]},
]


def _load_themes() -> list[dict]:
    raw = os.environ.get("THEMES_JSON")
    if not raw:
        return _DEFAULT_THEMES
    try:
        import json
        parsed = json.loads(raw)
        out = []
        for t in parsed:
            tid, label = str(t["id"]).strip(), str(t["label"]).strip()
            syms = [str(x).strip().upper() for x in t["symbols"] if str(x).strip()]
            if tid and label and syms:
                out.append({"id": tid, "label": label, "symbols": syms})
        # A malformed override must not silently empty the nav.
        return out or _DEFAULT_THEMES
    except Exception:
        return _DEFAULT_THEMES


THEMES = _load_themes()


CRYPTO_UNIVERSE = [
    s.strip() for s in os.environ.get(
        "CRYPTO_UNIVERSE", "BTC/USD:Bitcoin,ETH/USD:Ethereum,SOL/USD:Solana,"
                           "XRP/USD:XRP,DOGE/USD:Dogecoin,ADA/USD:Cardano,"
                           "AVAX/USD:Avalanche,LINK/USD:Chainlink,DOT/USD:Polkadot,"
                           "LTC/USD:Litecoin,BCH/USD:Bitcoin Cash,UNI/USD:Uniswap,"
                           "AAVE/USD:Aave,SHIB/USD:Shiba Inu,PEPE/USD:Pepe,"
                           "ARB/USD:Arbitrum,POL/USD:Polygon,FIL/USD:Filecoin,"
                           "RENDER/USD:Render,ONDO/USD:Ondo"
    ).split(",") if s.strip()
]
