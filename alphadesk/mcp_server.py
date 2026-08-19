"""AlphaDesk as an MCP server — the terminal's data, callable by an agent.

Every tool here calls the same functions the web UI does, which means the
attribution guarantees come with them: `screener_ask`, `filing_ask` and
`research_ask` return their citations, and a claim whose source could not be
verified was already dropped server-side before the agent ever sees the text.
That is the point of exposing this over MCP rather than handing an agent a
scraper — the agent inherits the discipline instead of having to reproduce it.

Read-only by construction. AlphaDesk holds no positions and places no orders,
so there is no write surface to expose or guard.

Run it:

    python -m alphadesk.main mcp              # stdio, for a local agent
    python -m alphadesk.main mcp --http       # streamable HTTP

Point an MCP client at the stdio command, e.g. for Claude Desktop:

    {"mcpServers": {"alphadesk": {
        "command": "python", "args": ["-m", "alphadesk.main", "mcp"]}}}
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

log = logging.getLogger("alphadesk.mcp")

mcp = FastMCP(
    "alphadesk",
    instructions=(
        "AlphaDesk is a read-only market research terminal. Tools return market "
        "data, SEC filings, news and AI answers about US equities.\n\n"
        "The three *_ask tools answer questions and return a `citations` list. "
        "Every citation was verified server-side against a record this server "
        "fetched — an article, a numbered calendar row, a pre-fetched data "
        "section, or a verbatim substring of the actual SEC document. Claims "
        "that could not be verified were REMOVED from the answer before it was "
        "returned. Prefer quoting these answers with their citations over "
        "restating them, and treat an empty citations list as a signal that the "
        "answer is weakly supported.\n\n"
        "Nothing here trades, holds positions, or gives investment advice."
    ),
)


# ── Market data ────────────────────────────────────────────────────────────

@mcp.tool()
def market_tape() -> list[dict]:
    """Index, rate, commodity and crypto levels: the top-of-terminal strip.

    Returns [{symbol, label, price, change_pct}].
    """
    from alphadesk.providers import get_prices
    return get_prices().market_tape()


@mcp.tool()
def quote(symbol: str) -> dict:
    """Full quote for one US-listed symbol: price and change, bid/ask, day and
    52-week ranges, volume, market cap, valuation multiples, beta, EPS and
    analyst targets.
    """
    from alphadesk.providers import get_prices
    q = get_prices().quote(symbol)
    if not q:
        raise ValueError(f"no quote available for {symbol!r}")
    return q


@mcp.tool()
def movers(top: int = 20) -> dict:
    """Most active, gainers and losers.

    Filtered for tradeability: warrants, rights and units are excluded, and
    rows must clear a price and dollar-volume floor. Note that gainers/losers
    skew small-cap — a percentage screen over the whole market always does.
    Large names appear on most_active, which ranks by volume.
    """
    from alphadesk.providers import get_prices
    return get_prices().movers(top=max(1, min(top, 50)))


@mcp.tool()
def price_chart(symbol: str, days: int = 2) -> dict:
    """Intraday OHLC with RSI-9 and MACD(12,26,9).

    IMPORTANT: check `indicators_reliable` before using the indicator series.
    On a sparse feed an illiquid name's "1-minute" bars can be a handful of
    prints stretched across days, which computes indicators that look normal
    and mean nothing. `coverage` and `median_gap_min` say how real the series
    is. When it is false, describe price only.
    """
    from alphadesk.providers import get_prices
    series = get_prices().chart_series(symbol, days=max(1, min(days, 30)))
    if not series:
        raise ValueError(f"no intraday bars for {symbol!r}")
    # The bar array is large and rarely what an agent wants; summarize it and
    # keep every field that speaks to whether the data can be trusted.
    bars = series.get("bars") or []
    return {
        "symbol": series["symbol"],
        "bar_count": series["bar_count"],
        "sessions": series["sessions"],
        "coverage": series["coverage"],
        "median_gap_min": series["median_gap_min"],
        "indicators_reliable": series["indicators_reliable"],
        "first_bar": bars[0] if bars else None,
        "last_bar": bars[-1] if bars else None,
        "rsi_9_last": next((v for v in reversed(series.get("rsi_9") or []) if v is not None), None),
        "macd_last": next((v for v in reversed(series.get("macd") or []) if v is not None), None),
    }


# ── The window ─────────────────────────────────────────────────────────────

@mcp.tool()
def screener_window() -> list[dict]:
    """Every symbol currently in view — those with fresh news or a report due
    inside the horizon — alphabetically, with recent headlines.

    Deliberately UNRANKED. The order carries no opinion; do not present it as
    a recommendation or a top list.
    """
    from alphadesk.desk import screener
    return screener.inventory()


@mcp.tool()
def screener_ask(question: str) -> dict:
    """Ask one question of the ENTIRE window at once — every article and
    upcoming report across every symbol, in a single pass.

    Returns {answer, citations, considered}. Each citation resolves to a stored
    article or calendar row; `considered` reports how much was actually read,
    which is the honest scope of the answer.
    """
    from alphadesk.desk import screener
    result = screener.ask(question)
    if result is None:
        raise ValueError("nothing in the current window, or the model call failed")
    return result


# ── SEC filings ────────────────────────────────────────────────────────────

@mcp.tool()
def list_filings(symbol: str) -> list[dict]:
    """A symbol's recent 10-K, 10-Q and 8-K filings from SEC EDGAR.

    Use the `accession` from a row here to call filing_ask.
    """
    from alphadesk.desk import filings
    return filings.list_filings(symbol)


@mcp.tool()
def filing_ask(accession: str, question: str) -> dict:
    """Ask a question of ONE SEC filing, answered only from that document.

    Returns {answer, citations} where every citation is a VERBATIM quote
    checked as a real substring of the filing text. A quote that did not verify
    was dropped. Empty citations means nothing in the answer could be tied to
    the document — treat it with suspicion.

    Get `accession` from list_filings.
    """
    from alphadesk.desk import filings
    result = filings.ask(accession, question)
    if result is None:
        raise ValueError("filing text unavailable, or the model call failed")
    return result


@mcp.tool()
def research_ask(symbol: str, question: str) -> dict:
    """Ask about one symbol using its fundamentals, institutional ownership,
    insider trades, earnings history, macro conditions and sector performance.

    All six sections are fetched by this server first, then answered in one
    pass. Returns {answer, citations, sections} — citations point at the
    numbered section they came from, and a citation naming a section that
    failed to fetch was dropped.
    """
    from alphadesk.desk import research
    result = research.ask(symbol, question)
    if result is None:
        raise ValueError("no usable data for that symbol, or the model call failed")
    return result


# ── Calendar ───────────────────────────────────────────────────────────────

@mcp.tool()
def earnings_calendar(days_ahead: int = 7) -> list[dict]:
    """Companies reporting within the next `days_ahead` days."""
    from alphadesk.ledger import store
    return store.upcoming_earnings(days=max(1, min(days_ahead, 60)))


@mcp.tool()
def recently_reported(days_back: int = 3) -> list[dict]:
    """Companies that reported in the last `days_back` days, with EPS actual vs
    estimate where the calendar has filled it in."""
    from alphadesk.ledger import store
    return store.recently_reported(days=max(1, min(days_back, 30)))


def serve(http: bool = False) -> None:
    """Start the MCP server. stdio by default; streamable HTTP with `http`."""
    from alphadesk.ledger import store
    store.init()
    mcp.run(transport="streamable-http" if http else "stdio")
