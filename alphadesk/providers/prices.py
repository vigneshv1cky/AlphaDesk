"""The built-in price provider: Alpaca for live trades and intraday bars,
yfinance for company and macro data.

This is a thin adapter over `ingest/prices.py` rather than a rewrite. That
module carries hard-won behaviour — the coverage statistics behind the chart's
data-quality gate, per-call TTL caches, indicator math that has to match
between the chart and everything else. Wrapping it declares the seam without
pretending the seam is free: an alternate provider has to satisfy the same
contract, including reporting honestly when its bars are too sparse to support
an indicator.
"""

from __future__ import annotations

from alphadesk.providers.registry import register


class BuiltinPrices:
    """Alpaca + yfinance. Config: ALPACA_API_KEY / ALPACA_SECRET_KEY."""

    name = "builtin"

    def context(self, symbol: str) -> dict | None:
        from alphadesk.ingest import prices
        return prices.get_context(symbol)

    def chart_series(self, symbol: str, days: int = 2) -> dict | None:
        from alphadesk.ingest import prices
        return prices.get_chart_series(symbol, days=days)

    def fundamentals(self, symbol: str) -> dict | None:
        from alphadesk.ingest import prices
        return prices.get_fundamentals(symbol)

    def institutional_ownership(self, symbol: str) -> dict | None:
        from alphadesk.ingest import prices
        return prices.get_institutional_ownership(symbol)

    def earnings_context(self, symbol: str) -> dict | None:
        from alphadesk.ingest import prices
        return prices.get_earnings_context(symbol)

    def macro(self) -> dict | None:
        from alphadesk.ingest import prices
        return prices.macro_snapshot()

    def sector_change_pct(self, sector: str | None) -> float | None:
        from alphadesk.ingest import prices
        return prices.sector_change_pct(sector)


register("prices", BuiltinPrices.name, BuiltinPrices)
