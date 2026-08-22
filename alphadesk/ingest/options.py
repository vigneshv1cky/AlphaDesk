"""Option chains, read-only.

WHAT THIS FEED DOES AND DOES NOT CARRY. Alpaca returns a quote and a last trade
per contract, and the contracts endpoint returns strike, expiry and open
interest. It does NOT return implied volatility or greeks — both come back null
on this entitlement, verified against NVDA. So there is no IV column and no
delta column here, rather than a column of dashes that looks like missing data
for one symbol instead of a capability the feed lacks. If that entitlement
changes, the fields are already in the response shape and only the table needs
a column.

Two upstream calls per chain, merged on the OCC symbol: the contracts endpoint
knows strike/expiry/open interest, the chain snapshot knows bid/ask/last.
Neither knows both.

This is a consumption surface like every other one here. Nothing prices an
option, scores a spread or suggests a trade.
"""

import logging
import os
import time
from datetime import date, timedelta
from typing import Optional

from alphadesk.net import bound_timeout as _bound

log = logging.getLogger("alphadesk.options")

_EXP_TTL_S = 600          # expiries move once a day at most
_CHAIN_TTL_S = 60         # quotes inside a chain move constantly
_MAX_EXPIRIES = 12
_HORIZON_DAYS = int(os.environ.get("OPTIONS_HORIZON_DAYS", "120"))

_exp_cache: dict[str, tuple[float, list[str]]] = {}
_chain_cache: dict[str, tuple[float, dict]] = {}


def _clients():
    from alpaca.data.historical.option import OptionHistoricalDataClient
    from alpaca.trading.client import TradingClient
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("no ALPACA keys")
    return (_bound(TradingClient(key, secret, paper=True)),
            _bound(OptionHistoricalDataClient(key, secret)))


def _contracts(symbol: str, expiry: Optional[str] = None, limit: int = 1000) -> list:
    from alpaca.trading.requests import GetOptionContractsRequest
    trading, _ = _clients()
    kw: dict = {"underlying_symbols": [symbol], "limit": limit}
    if expiry:
        kw["expiration_date"] = expiry
    else:
        kw["expiration_date_gte"] = date.today()
        kw["expiration_date_lte"] = date.today() + timedelta(days=_HORIZON_DAYS)
    res = trading.get_option_contracts(GetOptionContractsRequest(**kw))
    return list(getattr(res, "option_contracts", []) or [])


def expirations(symbol: str) -> list[str]:
    """Upcoming expiries for `symbol`, soonest first. Empty when the symbol has
    no listed options — which is a real answer, not a failure."""
    sym = symbol.upper()
    hit = _exp_cache.get(sym)
    if hit and time.time() - hit[0] < _EXP_TTL_S:
        return hit[1]
    try:
        rows = _contracts(sym)
    except Exception as exc:
        log.debug("expirations failed %s: %s", sym, exc)
        return hit[1] if hit else []
    out = sorted({str(c.expiration_date) for c in rows})[:_MAX_EXPIRIES]
    if out:
        _exp_cache[sym] = (time.time(), out)
    return out


def _merge(contracts, snaps) -> tuple[list[dict], list[dict]]:
    """Join contract metadata to its quote, split by side, sort by strike.

    Split out from the fetch so the rules that matter are testable without a
    network client: that a one-sided book yields no mid, and that both sides
    come back in strike order.
    """
    calls: list[dict] = []
    puts: list[dict] = []
    for c in contracts:
        occ = getattr(c, "symbol", "")
        snap = snaps.get(occ) if snaps else None
        q = getattr(snap, "latest_quote", None) if snap else None
        t = getattr(snap, "latest_trade", None) if snap else None
        bid = getattr(q, "bid_price", None) if q else None
        ask = getattr(q, "ask_price", None) if q else None
        last = getattr(t, "price", None) if t else None
        row = {
            "symbol": occ,
            "strike": float(getattr(c, "strike_price", 0) or 0),
            "bid": round(float(bid), 2) if bid is not None else None,
            "ask": round(float(ask), 2) if ask is not None else None,
            "last": round(float(last), 2) if last else None,
            # Midpoint only when BOTH sides quote. One-sided books are common
            # far from the money, and a mid computed off a single side is a made
            # up price rather than a wide one.
            "mid": round((float(bid) + float(ask)) / 2, 2)
                   if bid is not None and ask is not None else None,
            "open_interest": int(getattr(c, "open_interest", 0) or 0),
        }
        side = str(getattr(c, "type", "")).lower()
        (calls if "call" in side else puts).append(row)
    calls.sort(key=lambda r: r["strike"])
    puts.sort(key=lambda r: r["strike"])
    return calls, puts


def chain(symbol: str, expiry: str) -> dict:
    """{expiry, underlying, calls, puts} for one expiry.

    Rows are ordered by STRIKE, ascending, for both sides — the order a chain is
    read in. Not by volume, not by moneyness: an option chain is a price ladder
    and reordering it destroys the only structure it has.
    """
    sym, exp = symbol.upper(), expiry.strip()
    ck = f"{sym}:{exp}"
    hit = _chain_cache.get(ck)
    if hit and time.time() - hit[0] < _CHAIN_TTL_S:
        return hit[1]

    from alpaca.data.requests import OptionChainRequest
    try:
        _, data = _clients()
        contracts = _contracts(sym, expiry=exp)
        snaps = data.get_option_chain(
            OptionChainRequest(underlying_symbol=sym, expiration_date=exp))
    except Exception as exc:
        log.debug("chain failed %s %s: %s", sym, exp, exc)
        return hit[1] if hit else {}

    calls, puts = _merge(contracts, snaps)
    result = {"symbol": sym, "expiry": exp, "calls": calls, "puts": puts}
    if calls or puts:
        _chain_cache[ck] = (time.time(), result)
    return result
