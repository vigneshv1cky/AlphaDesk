"""Ledger round-trips. The store had no coverage at all before this."""



def test_init_drops_the_retired_trading_tables(store):
    """A pre-existing ledger loses its trading tables on first init.

    This is DESTRUCTIVE by design — the execution layer is gone and these
    outlived the code that wrote them — so it is worth a test that says so out
    loud rather than leaving it as a surprise.
    """
    import sqlite3

    dead = ["picks", "runs", "funnel", "skips", "price_daily", "earnings_reactions"]
    con = sqlite3.connect(store._DB)
    for t in dead:
        con.execute(f"CREATE TABLE IF NOT EXISTS {t} (id INTEGER PRIMARY KEY, junk TEXT)")
        con.execute(f"INSERT INTO {t} (junk) VALUES ('legacy row')")
    con.commit()
    con.close()

    store.init()

    con = sqlite3.connect(store._DB)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert not (set(dead) & names), f"still present after init: {set(dead) & names}"
    # and the tables the terminal actually uses survived
    assert {"news_articles", "earnings", "filings", "research_cache", "token_usage"} <= names


def test_articles_round_trip_and_group_by_ticker(store):
    store.save_articles([
        {"id": "a1", "title": "Apple beats", "summary": "s", "source": "Reuters",
         "url": "https://x/1", "published_at": "2026-08-18T10:00:00Z",
         "tickers": ["AAPL", "MSFT"]},
        {"id": "a2", "title": "Msft news", "summary": "", "source": "BBG",
         "url": "https://x/2", "published_at": "2026-08-18T09:00:00Z", "tickers": ["MSFT"]},
    ])
    by_ticker = store.recent_articles_by_ticker("2026-08-01T00:00:00Z")
    assert set(by_ticker) == {"AAPL", "MSFT"}
    # a multi-ticker article shows up under EVERY symbol it mentions — the
    # screener window depends on this
    assert len(by_ticker["MSFT"]) == 2
    assert by_ticker["MSFT"][0]["title"] == "Apple beats"   # newest first


def test_saving_the_same_article_twice_does_not_duplicate(store):
    art = {"id": "dupe", "title": "t", "summary": "", "source": "s", "url": "u",
           "published_at": "2026-08-18T10:00:00Z", "tickers": ["AAPL"]}
    store.save_articles([art])
    store.save_articles([art])
    assert len(store.recent_articles_by_ticker("2026-01-01T00:00:00Z")["AAPL"]) == 1


def test_digest_cache_is_keyed_on_the_exact_input(store):
    store.save_digest("*SCREENER-ASK*", "hash-a", "answer A", [{"item": 1}], "m")
    assert store.get_digest("*SCREENER-ASK*", "hash-a")["digest"] == "answer A"
    # a different input set must MISS, or the terminal would answer a new
    # question with a stale answer
    assert store.get_digest("*SCREENER-ASK*", "hash-b") is None


def test_research_cache_respects_its_ttl(store):
    import sqlite3

    store.save_research("AAPL", "qh", "q", "answer", [], [{"title": "T", "data": {}}], "m")
    assert store.get_research("AAPL", "qh", ttl_hours=4) is not None
    # the cache is per (symbol, question), not per question alone
    assert store.get_research("MSFT", "qh", ttl_hours=4) is None

    # Backdate the row rather than passing ttl_hours=0: the DATA behind a
    # research answer ages even when the question doesn't, and expiry by
    # wall-clock is the whole reason this cache has a TTL at all.
    con = sqlite3.connect(store._DB)
    con.execute("UPDATE research_cache SET generated_at = datetime('now','-5 hours')")
    con.commit()
    con.close()
    assert store.get_research("AAPL", "qh", ttl_hours=4) is None
    assert store.get_research("AAPL", "qh", ttl_hours=6) is not None


def test_filing_text_and_qa_round_trip(store):
    store.save_filings([{"accession": "0001", "symbol": "AAPL", "cik": "0000320193",
                         "form": "10-Q", "filing_date": "2026-07-31",
                         "report_date": "2026-06-30", "primary_doc": "d.htm",
                         "url": "https://sec/d.htm"}])
    assert store.get_filings("AAPL")[0]["form"] == "10-Q"
    assert store.get_filing_meta("0001")["url"] == "https://sec/d.htm"
    store.save_filing_text("0001", "the filing text")
    assert store.get_filing_text("0001") == "the filing text"
    store.save_filing_qa("0001", "qh", "q", "a", [{"quote": "the filing"}], "m")
    assert store.get_filing_qa("0001", "qh")["citations"][0]["quote"] == "the filing"


def test_token_spend_is_recorded_per_role(store):
    store.record_tokens("screener-ask", "some-model", 100, 20, None, "polygon")
    store.record_tokens("filing-qa", "some-model", 50, 5, None, "edgar")
    rows = {r["role"]: r for r in store.token_summary(days=1)}
    assert rows["screener-ask"]["input_tok"] == 100
    assert rows["filing-qa"]["calls"] == 1


def test_upcoming_earnings_window(store):
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=2)).isoformat()
    far = (date.today() + timedelta(days=60)).isoformat()
    store.upsert_earnings([
        {"symbol": "AAA", "report_date": soon, "session": "BMO", "eps_estimate": 1.0},
        {"symbol": "ZZZ", "report_date": far, "session": "AMC", "eps_estimate": 2.0},
    ])
    got = {e["symbol"] for e in store.upcoming_earnings(days=5)}
    assert "AAA" in got and "ZZZ" not in got


class TestPruneDelistedEarnings:
    """Rows upstream withdrew must go; rows we simply failed to fetch must not.

    The second half is the dangerous one — treating a failed fetch as "nothing
    reports that day" would delete a good day's calendar and the next refresh
    would put it straight back, so the damage would flicker rather than show.
    """

    def _seed(self, store):
        store.upsert_earnings([
            {"symbol": "AAA", "report_date": "2026-08-17", "company_name": "A Corp"},
            {"symbol": "BBB", "report_date": "2026-08-17", "company_name": "B Corp"},
            {"symbol": "CCC", "report_date": "2026-08-18", "company_name": "C Corp"},
        ])

    def test_a_withdrawn_report_is_dropped(self, store):
        self._seed(store)
        removed = store.prune_delisted_earnings({"2026-08-17": {"AAA"}})
        assert removed == 1
        left = {r["symbol"] for r in store.earnings_between("2026-08-17", "2026-08-17")}
        assert left == {"AAA"}, "BBB was no longer listed upstream"

    def test_other_days_are_untouched(self, store):
        self._seed(store)
        store.prune_delisted_earnings({"2026-08-17": {"AAA"}})
        assert [r["symbol"] for r in store.earnings_between("2026-08-18", "2026-08-18")] == ["CCC"]

    def test_a_failed_fetch_deletes_nothing(self, store):
        """An empty symbol set means the fetch told us nothing, NOT that the
        day is empty. Deleting on it would wipe a real day."""
        self._seed(store)
        assert store.prune_delisted_earnings({"2026-08-17": set()}) == 0
        assert len(store.earnings_between("2026-08-17", "2026-08-17")) == 2

    def test_a_day_absent_from_the_pull_is_untouched(self, store):
        self._seed(store)
        store.prune_delisted_earnings({"2026-08-18": {"CCC"}})
        assert len(store.earnings_between("2026-08-17", "2026-08-17")) == 2

    def test_company_name_survives_a_refresh_that_omits_it(self, store):
        """COALESCE, not overwrite — a later pull without the name must not
        blank one we already stored."""
        self._seed(store)
        store.upsert_earnings([{"symbol": "AAA", "report_date": "2026-08-17",
                                "eps_actual": 1.5}])
        row = store.earnings_between("2026-08-17", "2026-08-17")[0]
        assert row["company_name"] == "A Corp"
