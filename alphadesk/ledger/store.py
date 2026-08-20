"""The data store — SQLite (WAL).

Everything the terminal caches or accumulates: news articles and their
enrichment, SEC filings with their text and Q&A caches, research answers, the
earnings calendar, and token spend.

There is no trading ledger here. The picks/runs/funnel/skips tables that used
to live in this file were dropped on 2026-08-18 along with the execution layer,
and `init()` removes them from pre-existing databases.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from alphadesk.config import DATA_DIR

_DB = DATA_DIR / "ledger.db"
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    role        TEXT NOT NULL,
    model       TEXT NOT NULL,
    input_tok   INTEGER NOT NULL,
    output_tok  INTEGER NOT NULL,
    decision_id TEXT,
    source      TEXT             -- ingestion source this call served (FINANCIAL|EARNINGS|WORLD|SPILLOVER); NULL = cross-source
);

-- Earnings calendar: who reported (with the EPS surprise) and who's about to.
-- Drives "be ready" (upcoming) + post-earnings-drift candidates (recently reported).
CREATE TABLE IF NOT EXISTS earnings (
    symbol       TEXT NOT NULL,
    report_date  TEXT NOT NULL,     -- report date, YYYY-MM-DD (date-only, stable key)
    session      TEXT,              -- BMO (pre-open) | AMC (post-close) | DAY
    eps_estimate REAL,
    eps_actual   REAL,              -- NULL until reported
    surprise_pct REAL,              -- NULL until reported
    market_cap   REAL,              -- for ranking big names in the reporting-soon view
    company_name TEXT,              -- Nasdaq supplies it; a calendar of bare tickers is unreadable
    fetched_at   TEXT,
    UNIQUE(symbol, report_date) ON CONFLICT REPLACE
);
CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings (report_date);

-- Persistent enrichment cache: an article's sentiment/category never changes, so
-- enrich it once and reuse forever. Kills the biggest recurring token cost —
-- re-enriching the same overlapping news on every run/restart.
CREATE TABLE IF NOT EXISTS enrichment_cache (
    article_id       TEXT PRIMARY KEY,
    sentiment        REAL,     -- article-level (fallback / single-ticker)
    label            TEXT,
    category         TEXT,
    relations        TEXT,     -- JSON [{a, rel, b}]
    ticker_sentiment TEXT,     -- JSON {TICKER: {sentiment, label}} — per-company overrides
    ts               TEXT
);

-- Raw ticker-tagged articles (ingest/news.py). enrichment_cache holds the
-- LLM's opinion of an article keyed by id; this holds the article itself
-- (title/url/tickers) so the screener has something to render and a human
-- has something to click through to — attribution needs the URL, not just
-- the sentiment score.
CREATE TABLE IF NOT EXISTS news_articles (
    article_id   TEXT PRIMARY KEY,
    title        TEXT,
    summary      TEXT,
    source       TEXT,
    url          TEXT,
    published_at TEXT,
    tickers      TEXT,      -- JSON list, e.g. ["AAPL","NVDA"]
    ingested_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_articles (published_at);

-- Cached screener answers (desk/screener.py). Keyed on a hash of the exact
-- input set that produced the answer, so re-asking while no new news has
-- landed is a cache hit and costs nothing — the same amortization pattern as
-- enrichment_cache, one level up.
--
-- Rows are written under the sentinel symbol '*SCREENER-ASK*' (no real ticker
-- contains '*'), because an ask now spans the WHOLE window rather than one
-- symbol: the screener stopped ranking symbols and auto-narrating the top N,
-- so there are no longer per-symbol digests. Historical per-symbol rows from
-- that era may still be present; nothing reads them, and they age out with
-- the article ids in their hash.
CREATE TABLE IF NOT EXISTS symbol_digests (
    symbol       TEXT NOT NULL,   -- '*SCREENER-ASK*' for window-wide asks
    input_hash   TEXT NOT NULL,   -- sha1 of question + the sorted item ids behind it
    digest       TEXT,            -- the answer text
    citations    TEXT,            -- JSON [{kind, symbol, claim, title, url, source}]
    model        TEXT,
    generated_at TEXT,
    PRIMARY KEY (symbol, input_hash)
);

-- SEC EDGAR filing metadata (ingest/edgar.py). accession is SEC's own globally
-- unique id for one filing — the natural key, not an autoincrement.
CREATE TABLE IF NOT EXISTS filings (
    accession    TEXT PRIMARY KEY,
    symbol       TEXT NOT NULL,
    cik          TEXT NOT NULL,
    form         TEXT,            -- 10-K, 10-Q, 8-K, ...
    filing_date  TEXT,
    report_date  TEXT,
    primary_doc  TEXT,            -- filename within the accession's archive dir
    url          TEXT,            -- resolved sec.gov/Archives/... document URL
    ingested_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_filings_symbol ON filings (symbol, filing_date);

-- Extracted plain text per filing — parsing a multi-MB iXBRL document is
-- expensive (network + BeautifulSoup), so this is fetched once and reused for
-- every question asked against that filing afterward.
CREATE TABLE IF NOT EXISTS filing_text_cache (
    accession    TEXT PRIMARY KEY,
    text         TEXT,
    char_count   INTEGER,
    extracted_at TEXT
);

-- Cached AI answers, keyed on the exact (filing, question) pair — same
-- amortization pattern as symbol_digests. A rephrased question is a cache
-- miss on purpose: it may deserve a different answer.
CREATE TABLE IF NOT EXISTS filing_qa_cache (
    accession     TEXT NOT NULL,
    question_hash TEXT NOT NULL,
    question      TEXT,
    answer        TEXT,
    citations     TEXT,           -- JSON [{quote}] — verbatim snippets grep-able back into the source text
    model         TEXT,
    generated_at  TEXT,
    PRIMARY KEY (accession, question_hash)
);

-- Cached answers from the research agent (desk/research.py): all of one
-- symbol's fundamentals/ownership/insider/earnings/macro/sector data is
-- fetched server-side, then one LLM call answers from exactly that —
-- same "no claim without a source" shape as filing_qa_cache, keyed the same
-- way ((symbol, question) composite PK), but WITH a TTL that filing_qa_cache
-- doesn't need: a filing's text never changes, but a live quote or a recent
-- insider filing can go stale between two identical asks even though the
-- question text hasn't.
CREATE TABLE IF NOT EXISTS research_cache (
    symbol        TEXT NOT NULL,
    question_hash TEXT NOT NULL,
    question      TEXT,
    answer        TEXT,
    citations     TEXT,        -- JSON [{section, title, claim}]
    sections      TEXT,        -- JSON [{title, data}] — the real fetched data citations resolve against
    model         TEXT,
    generated_at  TEXT,
    PRIMARY KEY (symbol, question_hash)
);
CREATE INDEX IF NOT EXISTS idx_research_generated ON research_cache (generated_at);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _lock, _connect() as conn:
        # research_cache's primary key changed shape (question_hash alone ->
        # (symbol, question_hash)) when desk/research.py moved from a tool-
        # calling loop to per-symbol pre-fetched context — SQLite can't ALTER
        # a primary key, and this is purely a cache (a dropped row just means
        # the next identical ask re-runs instead of hitting a cache hit), so
        # drop-and-recreate is the correct migration, not an ADD COLUMN.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(research_cache)")}
        if cols and "symbol" not in cols:
            conn.execute("DROP TABLE research_cache")

        # One-time removal of the trading ledger (2026-08-18). AlphaDesk stopped
        # booking, holding and grading positions, and these tables outlived the
        # code that wrote them. This is DESTRUCTIVE and deliberate: an existing
        # database loses its decision history the first time this runs. Back up
        # ledger.db first if that history matters.
        for dead in ("picks", "runs", "funnel", "relationships", "relation_facts",
                     "skips", "earnings_reads", "price_daily", "ingest_stats",
                     "earnings_reactions"):
            conn.execute(f"DROP TABLE IF EXISTS {dead}")

        conn.executescript(_SCHEMA)

        # Idempotent column migrations for pre-existing databases — no-ops once
        # the column exists. Only the surviving tables are covered; the picks
        # migrations went with the table.
        try:
            conn.execute("ALTER TABLE earnings ADD COLUMN market_cap REAL")
        except sqlite3.OperationalError:
            pass  # already migrated
        for col in ("pre_report_close", "implied_move_pct"):   # pre-armed reporter context
            try:
                conn.execute(f"ALTER TABLE earnings ADD COLUMN {col} REAL")
            except sqlite3.OperationalError:
                pass  # already migrated
        try:
            conn.execute("ALTER TABLE earnings ADD COLUMN low_liquidity INTEGER")
        except sqlite3.OperationalError:
            pass  # already migrated
        try:
            conn.execute("ALTER TABLE earnings ADD COLUMN company_name TEXT")
        except sqlite3.OperationalError:
            pass  # already migrated
        try:
            conn.execute("ALTER TABLE token_usage ADD COLUMN source TEXT")
        except sqlite3.OperationalError:
            pass  # already migrated
        try:
            conn.execute("ALTER TABLE enrichment_cache ADD COLUMN ticker_sentiment TEXT")
        except sqlite3.OperationalError:
            pass  # already migrated


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _et_date(offset_days: int = 0) -> str:
    """Today's ET calendar date (± offset) as YYYY-MM-DD. The market clock and every
    stored report_date are ET, so date-window comparisons must key on the ET day — SQLite
    date('now') is UTC and shifts the window a day in the evening ET (dropping the oldest
    drift day / pulling in tomorrow's reporters)."""
    from datetime import timedelta

    from alphadesk.config import now_et
    return (now_et().date() + timedelta(days=offset_days)).isoformat()


def _et_day_start_utc() -> str:
    """UTC ISO timestamp of ET midnight today — for comparing full-timestamp
    columns (news_articles.ingested_at) against 'start of today' on the ET
    clock, which is the clock every stored report_date already uses."""
    from alphadesk.config import now_et
    return now_et().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(
        timezone.utc).isoformat()


def token_summary(days: int = 1) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, model, count(*) AS calls, sum(input_tok) AS input_tok,"
            " sum(output_tok) AS output_tok FROM token_usage"
            f" WHERE datetime(ts) >= datetime('now', '-{int(days)} day') GROUP BY role, model"
            " ORDER BY output_tok DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def record_tokens(role: str, model: str, input_tok: int, output_tok: int,
                  decision_id: str | None = None, source: str | None = None) -> None:
    """Log one LLM call's cost to token_usage — every call the news/screener
    pipeline makes passes through here, so /api/tokens stays an honest ledger
    of what the AI layer actually spent, not just the trade ledger."""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO token_usage (ts, role, model, input_tok, output_tok, decision_id, source)"
            " VALUES (?,?,?,?,?,?,?)",
            (_now(), role, model, int(input_tok), int(output_tok), decision_id, source))


def get_enrichment(article_ids: list[str]) -> dict[str, dict]:
    """Cached enrichments → {id: {sentiment,label,category,relations,ticker_sentiment}}."""
    if not article_ids:
        return {}
    ph = ",".join("?" * len(article_ids))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT article_id, sentiment, label, category, relations, ticker_sentiment"
            f" FROM enrichment_cache WHERE article_id IN ({ph})", article_ids
        ).fetchall()
    return {r["article_id"]: dict(r) for r in rows}


def save_articles(articles: list[dict]) -> None:
    """Persist raw articles (news_articles) — the record a human clicks through
    to, not the AI's opinion of it. INSERT OR IGNORE: an article's own facts
    (title/url/tickers) never change once published, and this can be called
    on the same overlapping poll window repeatedly without duplicating rows."""
    rows = [(a["id"], a.get("title"), a.get("summary"), a.get("source"), a.get("url"),
             a.get("published_at"), json.dumps(a.get("tickers") or []), _now())
            for a in (articles or [])]
    if not rows:
        return
    with _lock, _connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO news_articles"
            " (article_id, title, summary, source, url, published_at, tickers, ingested_at)"
            " VALUES (?,?,?,?,?,?,?,?)", rows)


def recent_articles_by_ticker(since_iso: str, limit_per_symbol: int = 12) -> dict[str, list[dict]]:
    """Recent articles grouped by ticker, newest first, capped per symbol so
    one chatty name (dozens of press-release wires) can't crowd out everyone
    else in the screener's context window."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT article_id, title, summary, source, url, published_at, tickers"
            " FROM news_articles WHERE published_at >= ? ORDER BY published_at DESC",
            (since_iso,)
        ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        for t in json.loads(d["tickers"] or "[]"):
            bucket = out.setdefault(t.upper(), [])
            if len(bucket) < limit_per_symbol:
                bucket.append(d)
    return out


def get_digest(symbol: str, input_hash: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT digest, citations, model, generated_at FROM symbol_digests"
            " WHERE symbol=? AND input_hash=?", (symbol.upper(), input_hash)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["citations"] = json.loads(d["citations"] or "[]")
    return d


def save_digest(symbol: str, input_hash: str, digest: str, citations: list[dict], model: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO symbol_digests"
            " (symbol, input_hash, digest, citations, model, generated_at)"
            " VALUES (?,?,?,?,?,?)",
            (symbol.upper(), input_hash, digest, json.dumps(citations), model, _now()))


def save_filings(rows: list[dict]) -> None:
    """Persist filing metadata. Each: {accession, symbol, cik, form,
    filing_date, report_date, primary_doc, url}. INSERT OR IGNORE: a filing's
    own facts never change once accepted by EDGAR."""
    data = [(r["accession"], r["symbol"].upper(), r["cik"], r.get("form"),
             r.get("filing_date"), r.get("report_date"), r.get("primary_doc"),
             r.get("url"), _now()) for r in (rows or [])]
    if not data:
        return
    with _lock, _connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO filings"
            " (accession, symbol, cik, form, filing_date, report_date, primary_doc, url, ingested_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)", data)


def get_filings(symbol: str, forms: list[str] | None = None, limit: int = 20) -> list[dict]:
    """A symbol's filings, newest first. forms=None returns every form type
    ever ingested for it (ingest/edgar.py only ever ingests 10-K/10-Q/8-K,
    so this is never actually unbounded in practice)."""
    with _connect() as conn:
        if forms:
            ph = ",".join("?" * len(forms))
            rows = conn.execute(
                f"SELECT accession, symbol, cik, form, filing_date, report_date, primary_doc, url"
                f" FROM filings WHERE symbol=? AND form IN ({ph})"
                f" ORDER BY filing_date DESC LIMIT ?",
                (symbol.upper(), *forms, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT accession, symbol, cik, form, filing_date, report_date, primary_doc, url"
                " FROM filings WHERE symbol=? ORDER BY filing_date DESC LIMIT ?",
                (symbol.upper(), limit)).fetchall()
    return [dict(r) for r in rows]


def get_filing_meta(accession: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT accession, symbol, cik, form, filing_date, report_date, primary_doc, url"
            " FROM filings WHERE accession=?", (accession,)).fetchone()
    return dict(row) if row else None


def get_filing_text(accession: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT text FROM filing_text_cache WHERE accession=?", (accession,)).fetchone()
    return row["text"] if row else None


def save_filing_text(accession: str, text: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO filing_text_cache (accession, text, char_count, extracted_at)"
            " VALUES (?,?,?,?)", (accession, text, len(text), _now()))


def get_filing_qa(accession: str, question_hash: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT question, answer, citations, model, generated_at FROM filing_qa_cache"
            " WHERE accession=? AND question_hash=?", (accession, question_hash)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["citations"] = json.loads(d["citations"] or "[]")
    return d


def save_filing_qa(accession: str, question_hash: str, question: str, answer: str,
                   citations: list[dict], model: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO filing_qa_cache"
            " (accession, question_hash, question, answer, citations, model, generated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (accession, question_hash, question, answer, json.dumps(citations), model, _now()))


def get_research(symbol: str, question_hash: str, ttl_hours: float) -> dict | None:
    """Cache hit only within ttl_hours of generation — unlike filing_qa_cache
    (whose key already encodes the exact input set, a filing's text that
    never changes), the underlying data here (a live quote, a recent insider
    filing) can go stale between two identical asks, so staleness has to be
    checked by wall-clock too."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT question, answer, citations, sections, model, generated_at FROM research_cache"
            " WHERE symbol=? AND question_hash=? AND datetime(generated_at) >= datetime('now', ?)",
            (symbol, question_hash, f"-{ttl_hours} hours")).fetchone()
    if not row:
        return None
    d = dict(row)
    d["citations"] = json.loads(d["citations"] or "[]")
    d["sections"] = json.loads(d["sections"] or "[]")
    return d


def save_research(symbol: str, question_hash: str, question: str, answer: str,
                  citations: list[dict], sections: list[dict], model: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO research_cache"
            " (symbol, question_hash, question, answer, citations, sections, model, generated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (symbol, question_hash, question, answer, json.dumps(citations), json.dumps(sections), model, _now()))


def save_enrichment(items: list[dict]) -> None:
    """Persist genuine enrichment results (not failure fallbacks). Each item:
    {article_id, sentiment, label, category, relations:list, ticker_sentiment:dict}."""
    rows = [(i["article_id"], i["sentiment"], i["label"], i["category"],
             json.dumps(i["relations"]), json.dumps(i.get("ticker_sentiment") or {}), _now())
            for i in (items or [])]
    if not rows:
        return
    with _lock, _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO enrichment_cache"
            " (article_id, sentiment, label, category, relations, ticker_sentiment, ts)"
            " VALUES (?,?,?,?,?,?,?)", rows)


def upsert_earnings(rows: list[dict]) -> None:
    """Insert/update earnings-calendar rows. Each: {symbol, report_date, session,
    eps_estimate, eps_actual, surprise_pct, market_cap}.

    Explicit UPSERT (not a bare INSERT relying on the table's ON CONFLICT REPLACE)
    so a recurring calendar refresh only touches the calendar-sourced columns —
    pre_report_close/implied_move_pct/low_liquidity are separately pre-armed off
    their own background passes and are comparatively expensive to recompute
    (low_liquidity alone is a batched yfinance download over every symbol in the
    window). A bare REPLACE silently wiped all three back to NULL on every
    refresh_calendar() call, so a live per-request read (or the next request
    right after any restart) saw stale/blank data despite the arming job having
    already run — that data loss is what this UPSERT prevents."""
    data = [(r["symbol"].upper(), r["report_date"], r.get("session"),
             r.get("eps_estimate"), r.get("eps_actual"), r.get("surprise_pct"),
             r.get("market_cap"), r.get("company_name"), _now())
            for r in (rows or []) if r.get("symbol") and r.get("report_date")]
    if not data:
        return
    with _lock, _connect() as conn:
        conn.executemany(
            "INSERT INTO earnings (symbol, report_date, session, eps_estimate,"
            " eps_actual, surprise_pct, market_cap, company_name, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(symbol, report_date) DO UPDATE SET"
            " session=excluded.session, eps_estimate=excluded.eps_estimate,"
            " eps_actual=excluded.eps_actual, surprise_pct=excluded.surprise_pct,"
            " market_cap=excluded.market_cap,"
            # COALESCE, not a plain overwrite: a later refresh that happens to
            # omit the name must not blank one we already have.
            " company_name=COALESCE(excluded.company_name, earnings.company_name),"
            " fetched_at=excluded.fetched_at", data)


def update_earnings_arm(symbol: str, report_date: str,
                        pre_close: float | None = None,
                        implied: float | None = None) -> None:
    """Store pre-armed context for an upcoming reporter: the pre-report close (the
    drift baseline) and the options-implied move. COALESCE keeps the EARLIEST arm
    (the closest to the report, before any post-announcement repricing)."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE earnings SET pre_report_close=COALESCE(pre_report_close, ?),"
            " implied_move_pct=COALESCE(implied_move_pct, ?)"
            " WHERE symbol=? AND report_date=?",
            (pre_close, implied, symbol.upper(), report_date))


def update_earnings_liquidity(low_liquidity: dict[str, bool]) -> int:
    """Persist the same 20d-avg-$vol liquidity bar the trading pipeline gates
    entries on, batch-computed off the earnings loop (prices.liquidity_batch),
    so the Earnings page reads a stored flag instead of live-fetching per
    request. Liquidity is a symbol property, not report-date-specific — sets
    it on every calendar row for that symbol."""
    if not low_liquidity:
        return 0
    with _lock, _connect() as conn:
        cur = conn.executemany(
            "UPDATE earnings SET low_liquidity=? WHERE symbol=?",
            [(int(v), sym.upper()) for sym, v in low_liquidity.items()])
        return cur.rowcount or 0


def prune_delisted_earnings(seen: dict[str, set[str]]) -> int:
    """Drop calendar rows upstream no longer lists.

    upsert_earnings only ever inserts and updates, so a report Nasdaq later
    reschedules or withdraws stays in the table forever. It then shows up as a
    ghost reporter and, worse, inflates the per-day call count the calendar
    leads with — measured on 2026-08-19: 37 rows on the Monday of which 11 had
    not been seen upstream since the 17th.

    `seen` maps a report date to EVERY symbol the upstream calendar returned
    for it, INCLUDING ones the tradability screen then dropped. Pruning against
    the pre-screen set means this only removes what upstream genuinely stopped
    listing, never something we merely chose not to store.

    A date whose fetch failed must not appear in `seen` at all — an empty set
    would read as "nothing reports that day" and delete a good day's rows.
    """
    removed = 0
    with _lock, _connect() as conn:
        for day, symbols in seen.items():
            if not symbols:
                continue                     # a failed fetch proves nothing
            ph = ",".join("?" * len(symbols))
            cur = conn.execute(
                f"DELETE FROM earnings WHERE report_date = ? AND symbol NOT IN ({ph})",
                (day, *(s.upper() for s in symbols)))
            removed += cur.rowcount or 0
    return removed


def purge_legacy_earnings() -> int:
    """Drop stale rows keyed by the OLD full-timestamp report_date (e.g.
    '2026-07-22T16:00:00-04:00'). The market-wide calendar now stores date-only
    keys, so these legacy rows would otherwise double every event. Idempotent."""
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM earnings WHERE report_date LIKE '%T%'")
        return cur.rowcount or 0


def recently_reported(days: int = 3) -> list[dict]:
    """Companies that reported in the last `days` — the post-earnings-drift candidate
    pool. NOT gated on eps_actual: Nasdaq backfills the actual EPS a day late, which
    hid every SAME-DAY reporter from the scout (the OTLY +30% miss). The caller
    (drift_candidates) filters to reports already PUBLIC and reads the price reaction;
    surprise_pct is often NULL until it lands, and drift direction comes from the
    reaction, not the result, so we don't wait for it."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, company_name, report_date, session, eps_estimate, eps_actual,"
            " surprise_pct, market_cap, pre_report_close, implied_move_pct, low_liquidity"
            " FROM earnings WHERE report_date >= ? AND report_date <= ?"
            # PRIORITY into the scout's capped window: freshest day first, then BIGGEST
            # by market cap. On a heavy day (~200 reporters) the scout only sees the top
            # slice, so ordering by size keeps the largest/most-tradeable names (a
            # mega-cap like GOOGL/TSLA) in view instead of scattering them past the cut
            # behind random micro-caps; the un-tradeable tail is what gets truncated.
            # Session/surprise are minor tiebreakers (NULLS LAST — unknown-cap last too).
            " ORDER BY report_date DESC,"
            "   market_cap IS NULL, market_cap DESC,"
            "   CASE session WHEN 'AMC' THEN 2 WHEN 'DAY' THEN 1 ELSE 0 END DESC,"
            "   surprise_pct IS NULL, surprise_pct DESC",
            (_et_date(-int(days)), _et_date(0)),
        ).fetchall()
    return [dict(r) for r in rows]


def earnings_window(days_back: int = 4, days_fwd: int = 14) -> list[dict]:
    """All calendar rows in [today-days_back, today+days_fwd] — reported AND
    upcoming, NOT gated on eps_actual. For the time-aware Calendar view, which
    splits reported/upcoming by when the report is public (see earnings.reported_public),
    not by whether Nasdaq has backfilled the actual EPS yet."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, company_name, report_date, session, eps_estimate, eps_actual,"
            " surprise_pct, market_cap, pre_report_close, implied_move_pct, low_liquidity"
            " FROM earnings WHERE report_date >= ? AND report_date <= ?"
            " ORDER BY report_date", (_et_date(-int(days_back)), _et_date(int(days_fwd))),
        ).fetchall()
    return [dict(r) for r in rows]


def earnings_between(start: str, end: str) -> list[dict]:
    """Every calendar row in [start, end], both YYYY-MM-DD and inclusive.

    Unlike earnings_window() this takes absolute dates rather than offsets from
    today, because the week view navigates away from today and "today ± N"
    cannot express "the week of the 16th".

    Ordered biggest-first within each day: a reporting day runs to ~50 names
    and the ones a reader is scanning for are at the top of that distribution.
    NULLS LAST so an unknown cap never outranks a known one.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, company_name, report_date, session, eps_estimate, eps_actual,"
            " surprise_pct, market_cap, low_liquidity FROM earnings"
            " WHERE report_date >= ? AND report_date <= ?"
            " ORDER BY report_date, market_cap IS NULL, market_cap DESC, symbol",
            (start, end)).fetchall()
    return [dict(r) for r in rows]


def upcoming_earnings(days: int = 7) -> list[dict]:
    """Companies REPORTING in the next `days` — the 'be ready' watch.

    Excludes a symbol that has ANOTHER row (any report_date) with eps_actual
    already filled in from the last few days. (symbol, report_date) is the
    upsert key (see upsert_earnings) — if Nasdaq corrects or re-pulls a
    report date, the old row doesn't get replaced, it just sits there
    alongside the new one. A symbol that already reported can still have a
    stale sibling row with eps_actual NULL, which would otherwise read as
    'reporting soon' and feed the PRE_EARNINGS momentum path for a print
    that's already happened (2026-08-13: INFQ, reported 08-12, still picked
    up here off a stale 08-13 row)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, company_name, report_date, session, eps_estimate, market_cap,"
            " pre_report_close, implied_move_pct, low_liquidity FROM earnings e1"
            " WHERE eps_actual IS NULL AND report_date >= ?"
            "   AND report_date <= ?"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM earnings e2 WHERE e2.symbol = e1.symbol"
            "       AND e2.eps_actual IS NOT NULL AND e2.report_date >= ?)"
            " ORDER BY report_date",
            (_et_date(0), _et_date(int(days)), _et_date(-3)),
        ).fetchall()
    return [dict(r) for r in rows]


def news_health() -> dict:
    """Is the news pipeline alive? Last article ingested, how many today, and
    today's AI spend — the one thing here that runs unattended and can fail
    silently (a dead feed or a dead model endpoint)."""
    with _connect() as conn:
        last_at = conn.execute(
            "SELECT max(ingested_at) FROM news_articles").fetchone()[0]
        today = int(conn.execute(
            "SELECT count(*) FROM news_articles WHERE ingested_at >= ?",
            (_et_day_start_utc(),)).fetchone()[0])
    tok = token_summary(days=1)
    return {
        "last_article_at": last_at,
        "articles_today": today,
        "tokens_today_in": sum(t["input_tok"] or 0 for t in tok),
        "tokens_today_out": sum(t["output_tok"] or 0 for t in tok),
        "calls_today": sum(t["calls"] or 0 for t in tok),
    }


init()
