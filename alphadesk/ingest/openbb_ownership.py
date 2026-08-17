"""SEC Form 4 insider trades — free, keyless, via OpenBB's SEC provider.

Calls the SEC provider's Fetcher class directly (`openbb_sec.models.
insider_trading.SecInsiderTradingFetcher`), bypassing OpenBB's `obb` router/
extension layer entirely — this repo doesn't need the ~50 other provider
packages that layer exists to serve, matching ingest/edgar.py's existing
"talk to SEC directly, no framework indirection" convention.

Institutional ownership (13F) is deliberately NOT here. Spiked live before
writing this: SEC's Form 13F is filed BY an institutional manager and lists
THEIR holdings across many companies — querying it by a held company's own
symbol (e.g. AAPL) returns nothing, because Apple isn't an institutional
filer. There's no free reverse index from "held company" back to "who holds
it" in raw SEC data (that aggregation is what paid providers like Fintel
sell). yfinance already gives the right shape for free — see
ingest/prices.py's get_institutional_ownership().
"""

import logging
import threading
import time

from alphadesk.config import OWNERSHIP_TTL_S

log = logging.getLogger("alphadesk.openbb_ownership")

_cache_lock = threading.Lock()
_insider_cache: dict[str, tuple[float, list[dict] | None]] = {}

# Fields a research citation would actually reference — drops noisier/rarely-
# populated columns (company_cik, owner_cik, ownership_type, form, other,
# other_text, transaction_timeliness, nature_of_ownership, exercise/expiration
# dates, underlying-security-* — all derivative-security bookkeeping, not
# what a "did an insider buy or sell" question needs).
_KEEP_FIELDS = (
    "symbol", "company_name", "filing_date", "transaction_date",
    "owner_name", "owner_title", "officer", "director", "ten_percent_owner",
    "transaction_type", "acquisition_or_disposition", "security_type",
    "securities_owned", "securities_transacted", "transaction_price",
    "transaction_value", "value_owned", "filing_url", "footnote",
)


def _clean(row: dict) -> dict:
    out = {}
    for k in _KEEP_FIELDS:
        if k not in row:
            continue
        v = row[k]
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


def get_insider_trades(symbol: str, limit: int = 20) -> list[dict] | None:
    """Recent Form 4 insider buy/sell activity for a symbol (best-effort,
    cached — event-driven, not a live feed). Returns None on any failure
    (missing/broken openbb-sec install, no filings, SEC outage) — never
    raises, so a broken optional dependency degrades only this one tool, not
    the process."""
    sym = symbol.upper()
    with _cache_lock:
        hit = _insider_cache.get(sym)
        if hit and time.time() - hit[0] < OWNERSHIP_TTL_S:
            return hit[1]
    out: list[dict] | None = None
    try:
        import asyncio

        from openbb_sec.models.insider_trading import SecInsiderTradingFetcher
        data = asyncio.run(
            SecInsiderTradingFetcher.fetch_data({"symbol": sym, "limit": limit}, {})
        )
        # fetch_data's declared return type is a union with AnnotatedResult
        # (used only when metadata-extraction kwargs are passed, which we
        # never do here) — at runtime with plain kwargs this is always the
        # bare list of results, confirmed live against real SEC data.
        cleaned = [_clean(row.model_dump()) for row in data]  # type: ignore[union-attr]
        out = cleaned or None
    except Exception as exc:
        log.debug("insider trades failed %s: %s", sym, exc)
    with _cache_lock:
        _insider_cache[sym] = (time.time(), out)
    return out
