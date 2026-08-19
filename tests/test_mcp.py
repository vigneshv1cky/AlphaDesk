"""The MCP surface.

Tools are checked by shape and contract rather than by calling upstreams: the
point of these tests is that the tool list, its descriptions and its wiring
stay correct, not that Yahoo is up.
"""

import pytest

from alphadesk import mcp_server


@pytest.fixture()
def tools():
    import asyncio
    return {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}


EXPECTED = {
    "market_tape", "quote", "movers", "price_chart",
    "screener_window", "screener_ask",
    "list_filings", "filing_ask", "research_ask",
    "earnings_calendar", "recently_reported",
}


def test_every_tool_is_exposed(tools):
    assert EXPECTED <= set(tools), f"missing: {EXPECTED - set(tools)}"


def test_no_tool_can_write(tools):
    """AlphaDesk is read-only. A tool that books, trades or mutates state must
    never appear here — this test is the tripwire if someone adds one."""
    forbidden = ("book", "order", "trade", "buy", "sell", "execute", "delete", "place")
    for name in tools:
        assert not any(f in name.lower() for f in forbidden), name


def test_every_tool_documents_itself(tools):
    """Descriptions are the only thing an agent has to choose a tool with."""
    for name, t in tools.items():
        assert t.description and len(t.description) > 40, f"{name} is under-documented"


def test_citation_tools_promise_citations(tools):
    """The three *_ask tools are the reason to expose this over MCP at all —
    their descriptions must tell an agent that citations exist and that
    unverifiable claims were removed."""
    for name in ("screener_ask", "filing_ask", "research_ask"):
        d = tools[name].description.lower()
        assert "citation" in d, f"{name} does not mention citations"


def test_server_instructions_state_the_guarantee():
    """An agent reads these before any tool. They must say the answers are
    verified and that the terminal does not trade."""
    ins = (mcp_server.mcp.instructions or "").lower()
    assert "verified" in ins
    assert "citations" in ins
    assert "read-only" in ins
    assert "nothing here trades" in ins


def test_chart_tool_surfaces_the_data_quality_gate(tools):
    """The chart tool must warn an agent about indicators_reliable, or an agent
    will happily describe RSI computed on a handful of prints."""
    d = tools["price_chart"].description
    assert "indicators_reliable" in d
    assert "coverage" in d


def test_bounded_params_are_clamped(monkeypatch):
    """An agent asking for 10000 movers must not become a 10000-row request."""
    seen = {}

    class FakePrices:
        name = "fake"

        def movers(self, top=20):
            seen["top"] = top
            return {"most_active": [], "gainers": [], "losers": []}

    from alphadesk.providers import registry
    registry.register("prices", "fake", FakePrices)
    monkeypatch.setenv("PRICE_PROVIDER", "fake")
    registry.reset_cache()

    mcp_server.movers(top=10_000)
    assert seen["top"] == 50
    mcp_server.movers(top=-1)
    assert seen["top"] == 1
