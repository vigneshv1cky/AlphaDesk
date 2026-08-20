"""Provider contracts.

These are `Protocol`s, not base classes, on purpose: a provider is anything
with the right shape. A third-party package doesn't import an AlphaDesk class
or inherit from it — it writes a plain object and registers it. That keeps the
dependency arrow pointing one way and means a provider can be tested with no
AlphaDesk imports at all.

Every method here is allowed to fail by raising `ProviderError`. Callers treat
that as "this source had nothing for me" and degrade — they never let a
provider failure take down a page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


class ProviderError(Exception):
    """A provider could not fulfil a request.

    The one exception type every provider raises. Callers catch this and drop
    the item; they must never have to know which vendor SDK is underneath or
    which of its twelve exception types leaked out.
    """


@dataclass(slots=True)
class Article:
    """One news item, normalized across feeds.

    `symbols` is the load-bearing field: the screener groups the whole window
    by ticker, so a feed that cannot tag an article with the symbols it is
    about cannot back this app. If a provider only does per-symbol queries, it
    should fill this in itself from the query it made.
    """

    id: str
    title: str
    url: str
    published_at: str          # ISO 8601
    symbols: list[str]
    summary: str = ""
    source: str = ""           # publisher name, not the provider name
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChatResult:
    """One LLM completion plus what it cost."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


@runtime_checkable
class LLMProvider(Protocol):
    """A text-in, JSON-out completion.

    Deliberately minimal. AlphaDesk only ever asks a model to read supplied
    text and return a JSON object — no streaming, no tool calls, no
    conversation state. A provider that supports more is free to; the app
    won't ask.
    """

    name: str

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 2048,
        timeout_s: float = 60.0,
    ) -> ChatResult:
        """Return a completion whose text is a JSON object.

        Implementations should request structured output natively where the
        API supports it (OpenAI-compatible `response_format`, Anthropic tool
        use) rather than relying on the prompt alone. Raise `ProviderError`
        on transport failure, auth failure, or unparseable output.
        """
        ...


@runtime_checkable
class NewsProvider(Protocol):
    """A source of ticker-tagged news."""

    name: str

    def fetch(self, since: datetime, limit: int = 200) -> list[Article]:
        """Articles published at or after `since`, NEWEST FIRST.

        Newest-first matters: callers apply a hard cap, so the only correct
        thing to sacrifice under that cap is the oldest news. Return an empty
        list rather than raising when the feed simply has nothing.
        """
        ...


@runtime_checkable
class PriceProvider(Protocol):
    """Quotes, bars and company data.

    The widest of the three interfaces, because it backs the chart, the
    earnings page and the research answers. Every method may return None to
    mean "not available from this source" — the UI already renders absence
    honestly (see the chart's data-quality gate), so a partial provider is
    usable rather than broken.
    """

    name: str

    def context(self, symbol: str) -> dict | None:
        """Last price, change, volume, ATR%, liquidity flags."""
        ...

    def chart_series(self, symbol: str, days: int = 2,
                     range_key: str | None = None) -> dict | None:
        """OHLC plus indicator series AND the coverage statistics that say
        whether those indicators can be trusted. A provider that cannot report
        coverage should report it as unreliable rather than omit it.

        `range_key` is one of 1D/5D/1M/3M/6M/YTD/1Y/5Y/MAX and selects the
        SERIES, not merely its length — a provider is expected to switch from
        intraday to daily bars past the reach of its minute feed rather than
        return a sparse intraday series stretched over a year."""
        ...

    def fundamentals(self, symbol: str) -> dict | None: ...
    def institutional_ownership(self, symbol: str) -> dict | None: ...
    def earnings_context(self, symbol: str) -> dict | None: ...
    def macro(self) -> dict | None: ...
    def sector_change_pct(self, sector: str | None) -> float | None: ...

    def quote(self, symbol: str) -> dict | None:
        """The equity-overview readout — price, bid/ask, ranges, valuation
        multiples, analyst targets. None if the source cannot price it."""
        ...

    def movers(self, top: int = 20) -> dict:
        """{most_active, gainers, losers}. Implementations should filter out
        instruments that are arithmetically large movers but informationally
        empty (sub-dollar tickers, warrants, near-zero turnover)."""
        ...

    def market_tape(self) -> list[dict]:
        """The index/commodity/crypto strip: [{symbol, label, price, change_pct}].
        Omit a symbol you cannot price rather than reporting it as zero — a tape
        showing 0.00 reads as a crashed market, not a missing quote."""
        ...
