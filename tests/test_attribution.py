"""The attribution rule: no claim renders without a source that checks out.

This is the project's central invariant and it is enforced three different
ways, one per caller. These tests pin the DROP behaviour specifically — it is
easy to write a citation resolver that passes bad citations through, and the
failure is silent and looks fine on screen.
"""

from alphadesk.desk import filings, research, screener


class TestScreenerCitations:
    """Cites by ITEM INDEX into a numbered window we built."""

    def items(self):
        return [
            {"kind": "earnings", "symbol": "MSFT", "report_date": "2026-01-05"},
            {"kind": "article", "symbol": "AAPL", "article_id": "a1",
             "title": "Apple beats", "url": "https://x/1", "source": "Reuters"},
        ]

    def test_resolves_to_the_stored_record(self):
        out = screener._resolve_citations([{"item": 2, "claim": "beat"}], self.items())
        assert len(out) == 1
        # the URL comes from OUR record, never from the model
        assert out[0]["url"] == "https://x/1"
        assert out[0]["symbol"] == "AAPL"

    def test_earnings_rows_are_citable_too(self):
        out = screener._resolve_citations([{"item": 1, "claim": "reports"}], self.items())
        assert out[0]["kind"] == "earnings" and out[0]["symbol"] == "MSFT"

    def test_out_of_range_index_is_dropped(self):
        for bad in (0, 3, 99, -1):
            assert screener._resolve_citations([{"item": bad, "claim": "x"}], self.items()) == []

    def test_non_integer_index_is_dropped(self):
        assert screener._resolve_citations([{"item": "2", "claim": "x"}], self.items()) == []
        assert screener._resolve_citations([{"claim": "no index"}], self.items()) == []


class TestFilingQuotes:
    """Quotes must be real substrings of the filing text."""

    TEXT = "The Company recorded net sales of $91.0 billion in the quarter."

    def test_verbatim_quote_survives(self):
        got = filings._verify_quotes(["recorded net sales of $91.0 billion"], self.TEXT)
        assert len(got) == 1

    def test_whitespace_differences_are_tolerated(self):
        got = filings._verify_quotes(["recorded   net sales\nof $91.0 billion"], self.TEXT)
        assert len(got) == 1, "reflowed whitespace is not a fabrication"

    def test_invented_quote_is_dropped(self):
        got = filings._verify_quotes(["recorded net sales of $250.0 billion"], self.TEXT)
        assert got == [], "a number the filing never said must not render"

    def test_too_short_to_be_evidence_is_dropped(self):
        assert filings._verify_quotes(["the"], self.TEXT) == []


class TestResearchSections:
    """Cites by SECTION INDEX into sections the server actually fetched."""

    def sections(self):
        return [
            {"title": "Fundamentals", "data": {"pe": 30}},
            {"title": "Insider trades", "data": {"available": False}},
        ]

    def test_valid_section_resolves(self):
        out = research._resolve_citations([{"section": 1, "claim": "PE is 30"}], self.sections())
        assert out[0]["title"] == "Fundamentals"

    def test_citation_to_an_unavailable_section_is_dropped(self):
        out = research._resolve_citations([{"section": 2, "claim": "insiders sold"}], self.sections())
        assert out == [], "a section that failed to fetch cannot support a claim"

    def test_out_of_range_and_malformed_are_dropped(self):
        s = self.sections()
        assert research._resolve_citations([{"section": 7, "claim": "x"}], s) == []
        assert research._resolve_citations([{"section": 1, "claim": "  "}], s) == []
        assert research._resolve_citations([{"section": "1", "claim": "x"}], s) == []
