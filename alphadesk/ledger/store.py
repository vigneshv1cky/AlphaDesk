"""The decision ledger — SQLite (WAL). Every evaluation, token, and funnel count.

One row per evaluation (team or solo). Closed-market picks carry
entry_price=NULL and are stamped with entry-at-next-open semantics by the
grader. All writes are single-process; the dashboard reads the same file.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from alphadesk.config import DATA_DIR

_DB = DATA_DIR / "ledger.db"
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,                 -- decision time UTC ISO
    symbol          TEXT NOT NULL,
    arm             TEXT NOT NULL,                 -- TEAM | LONER
    edge            TEXT,                          -- SPILLOVER | THEME | MOMENTUM
    trigger_src     TEXT NOT NULL,                 -- STREAM | DEEP_RUN | REPLAY
    session         TEXT NOT NULL,                 -- PRE | OPEN | AFTER | CLOSED
    -- decision
    direction       TEXT NOT NULL,                 -- LONG | SHORT
    horizon_days    INTEGER NOT NULL,
    score           REAL NOT NULL,                 -- pre-debate
    adjusted_score  REAL,                          -- post-debate (team only)
    confidence      REAL NOT NULL,
    verdict         TEXT,                          -- STRONG | SOFT | PASS
    approved        INTEGER NOT NULL DEFAULT 0,
    -- context
    triage_reason   TEXT,
    thesis          TEXT,
    debate          TEXT,                          -- JSON transcript
    briefs          TEXT,                          -- JSON
    model_tags      TEXT,                          -- JSON: stage → model actually used
    low_liquidity   INTEGER NOT NULL DEFAULT 0,
    -- attribution
    skeptic_moved_score REAL,
    arbiter_overrode    INTEGER DEFAULT 0,
    -- market snapshot
    entry_price     REAL,                          -- NULL when decided market-closed
    spy_price       REAL,
    -- actionable trade plan (execution desk): suggested levels for the committed call
    plan_entry      REAL,
    plan_target     REAL,
    plan_stop       REAL,
    plan_note       TEXT,
    -- outcomes
    ret_1d          REAL,
    ret_horizon     REAL,
    spy_ret_horizon REAL,
    alpha_net       REAL,
    graded_at       TEXT,
    -- position lifecycle: set when the Chief marks TAKE; re-evaluated on later runs
    taken           INTEGER NOT NULL DEFAULT 0,
    exit_ts         TEXT,                          -- early exit stamped by a re-eval
    exit_reason     TEXT,
    exit_price      REAL,                          -- price at exit (target/stop hit or review)
    exit_return_pct REAL,                          -- realized return entry→exit (direction-aware)
    exit_alpha      REAL,                          -- realized alpha vs SPY over the hold, net friction
    -- path while held: how far it ran / how far underwater BEFORE it closed
    mfe_pct         REAL,                          -- max favorable excursion (peak profit), % vs entry
    mae_pct         REAL                           -- max adverse excursion (worst drawdown), % vs entry
);
CREATE INDEX IF NOT EXISTS idx_picks_ts ON picks (ts);
CREATE INDEX IF NOT EXISTS idx_picks_symbol ON picks (symbol);

CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,                       -- PREMARKET | EVENING | ADHOC
    top_picks TEXT                                 -- JSON
);

CREATE TABLE IF NOT EXISTS funnel (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    window_ts  TEXT NOT NULL,
    ingested   INTEGER DEFAULT 0,
    candidates INTEGER DEFAULT 0,
    picked     INTEGER DEFAULT 0,
    skipped    INTEGER DEFAULT 0,
    skip_reasons TEXT                              -- JSON [{symbol, reason}]
);

CREATE TABLE IF NOT EXISTS relationships (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    from_sym    TEXT NOT NULL,      -- the shocked company
    to_sym      TEXT NOT NULL,      -- the exposed, tradable company
    direction   TEXT,              -- LONG | SHORT (the ripple's implied trade)
    chain       TEXT,              -- the causal chain, web-verified
    UNIQUE(from_sym, to_sym, direction) ON CONFLICT REPLACE
);
CREATE INDEX IF NOT EXISTS idx_rel_from ON relationships (from_sym);

-- News-stated inter-company relations (a SUPPLIES|COMPETES|PARTNERS b), extracted
-- by the enrichment from article TEXT and carrying the article URL as evidence.
-- The accumulating fact graph the connections desk reads before any LLM search.
CREATE TABLE IF NOT EXISTS relation_facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    from_sym   TEXT NOT NULL,
    to_sym     TEXT NOT NULL,
    rel        TEXT NOT NULL,        -- SUPPLIES | COMPETES | PARTNERS
    evidence   TEXT,                 -- article URL
    UNIQUE(from_sym, to_sym, rel) ON CONFLICT IGNORE
);
CREATE INDEX IF NOT EXISTS idx_relfact_from ON relation_facts (from_sym);
CREATE INDEX IF NOT EXISTS idx_relfact_to ON relation_facts (to_sym);

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

-- Per-run ingestion volume by source: how many articles came in from where, and
-- how many became candidates. Joined with token_usage.source + picks.source for
-- the source scorecard (cost + volume + value per ingestion channel).
CREATE TABLE IF NOT EXISTS ingest_stats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    source     TEXT NOT NULL,     -- FINANCIAL | EARNINGS | WORLD | SPILLOVER
    articles   INTEGER DEFAULT 0,
    candidates INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ingest_ts ON ingest_stats (ts);

-- Scout skips, graded forward for missed moves (anti-survivorship). A skip has
-- no direction, so 'missed' = a large |move vs SPY| we chose not to even look at.
CREATE TABLE IF NOT EXISTS skips (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    reason     TEXT,
    abs_alpha  REAL,        -- |symbol return − SPY| over the grade window, %
    missed     INTEGER,     -- 1 if abs_alpha crossed the miss threshold
    graded_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_skips_ts ON skips (ts);

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
    fetched_at   TEXT,
    UNIQUE(symbol, report_date) ON CONFLICT REPLACE
);
CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings (report_date);

-- LEGACY/unused: one web-grounded read per earnings event. The earnings brief is
-- now pure code-fetched facts (desk/earnings_reader.earnings_block) — no LLM read
-- exists to cache. Table kept for existing DBs; no longer written.
CREATE TABLE IF NOT EXISTS earnings_reads (
    symbol      TEXT NOT NULL,
    report_date TEXT NOT NULL,
    report_read TEXT,
    ts          TEXT,
    UNIQUE(symbol, report_date) ON CONFLICT REPLACE
);

-- Shadow A/B on the material-reaction gate: one row per public reporter (gate-passed
-- AND gate-dropped), graded forward vs SPY in the reaction direction. Lets us see
-- whether forward alpha turns on at the gate threshold or the gate cuts winners.
CREATE TABLE IF NOT EXISTS earnings_reactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,      -- first-sighting time (fixes the Model-A entry clock)
    symbol          TEXT NOT NULL,
    report_date     TEXT NOT NULL,      -- YYYY-MM-DD, stable event key
    session         TEXT,               -- BMO|AMC|DAY (drives the entry clock)
    direction       TEXT,               -- LONG|SHORT, from the reaction sign
    horizon_days    INTEGER,            -- fixed A/B horizon
    mkt_session     TEXT,               -- MARKET session at sighting (PRE|OPEN|AFTER|CLOSED) → Model-A entry clock
    reaction_total  REAL,               -- reaction % at sighting (the gate input)
    gate_passed     INTEGER,            -- 1 if |reaction| >= MATERIAL_REACTION_PCT
    low_liquidity   INTEGER DEFAULT 0,
    entry_price     REAL,               -- filled forward by the grader (next 9:30 open)
    ret_horizon     REAL,
    spy_ret_horizon REAL,
    alpha_net       REAL,               -- forward alpha vs SPY, reaction direction, net friction
    graded_at       TEXT,
    UNIQUE(symbol, report_date) ON CONFLICT IGNORE   -- one row per report event (first sighting wins)
);
CREATE INDEX IF NOT EXISTS idx_reactions_ts ON earnings_reactions (ts);

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

-- Local daily OHLC cache — backtests (and anything replaying history) fill this
-- once and never hammer yfinance again (its rate limiter throttles bulk downloads).
CREATE TABLE IF NOT EXISTS price_daily (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,      -- YYYY-MM-DD (calendar date)
    open   REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_pricedaily_sym ON price_daily (symbol);

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
-- fetched server-side, then one DeepSeek call answers from exactly that —
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
        conn.executescript(_SCHEMA)
        # idempotent migrations for pre-existing DBs (no-op once the column exists)
        for col, decl in (("taken", "INTEGER NOT NULL DEFAULT 0"),
                          ("exit_ts", "TEXT"), ("exit_reason", "TEXT"),
                          ("exit_price", "REAL"), ("exit_return_pct", "REAL"),
                          ("exit_alpha", "REAL"), ("mfe_pct", "REAL"),
                          ("mae_pct", "REAL")):
            try:
                conn.execute(f"ALTER TABLE picks ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # already migrated
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
        for col, decl in (("plan_entry", "REAL"), ("plan_target", "REAL"),
                          ("plan_stop", "REAL"), ("plan_note", "TEXT"),
                          ("source", "TEXT"), ("decision_id", "TEXT"),
                          ("order_type", "TEXT")):   # 'market' | 'limit' — how the entry fills (Model A)
            try:
                conn.execute(f"ALTER TABLE picks ADD COLUMN {col} {decl}")
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
        for col in ("beta", "alpha_adj"):   # honest-alpha prototype (beta-adjusted, borrow-aware)
            try:
                conn.execute(f"ALTER TABLE picks ADD COLUMN {col} REAL")
            except sqlite3.OperationalError:
                pass  # already migrated
        for col in ("sector", "cluster"):   # concentration cap / correlation clustering
            try:
                conn.execute(f"ALTER TABLE picks ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # already migrated
        for col, decl in (("broker_order_id", "TEXT"), ("broker_status", "TEXT"),
                          ("broker_qty", "REAL"),
                          ("broker_fill_price", "REAL"), ("broker_fill_ts", "TEXT")):
            try:   # paper portfolio manager (Alpaca); fill = the ledger entry when present
                conn.execute(f"ALTER TABLE picks ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # already migrated
        try:   # market session at sighting → the reaction A/B's Model-A entry clock
            conn.execute("ALTER TABLE earnings_reactions ADD COLUMN mkt_session TEXT")
        except sqlite3.OperationalError:
            pass  # already migrated
        try:   # capturable drift (from the first post-report open) — the honest miss gauge
            conn.execute("ALTER TABLE earnings_reactions ADD COLUMN reaction_drift REAL")
        except sqlite3.OperationalError:
            pass  # already migrated
        for col, decl in (("hedge_of", "INTEGER"),):
            try:   # macro hedge — companion SHORT protecting a LONG through overnight shock
                conn.execute(f"ALTER TABLE picks ADD COLUMN {col} {decl}")
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
    """UTC ISO timestamp of ET midnight today — for comparing full-timestamp columns
    (picks.ts) against 'start of today' on the ET clock."""
    from alphadesk.config import now_et
    return now_et().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(
        timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Picks
# ---------------------------------------------------------------------------

_JSON_FIELDS = ("debate", "briefs", "model_tags")


def _check_cols(keys) -> None:
    """Column names are interpolated into SQL (values are always bound), so every key
    MUST be a bare identifier. All callers pass literal keys today; this guard stops a
    future caller that lets a model-derived string become a column key from opening an
    injection hole (str.isidentifier() admits only [A-Za-z_][A-Za-z0-9_]*)."""
    for k in keys:
        if not isinstance(k, str) or not k.isidentifier():
            raise ValueError(f"invalid column name: {k!r}")


def _decode(row: dict) -> dict:
    for field in _JSON_FIELDS:
        if row.get(field):
            try:
                row[field] = json.loads(row[field])
            except Exception:
                pass
    return row


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
             r.get("market_cap"), _now())
            for r in (rows or []) if r.get("symbol") and r.get("report_date")]
    if not data:
        return
    with _lock, _connect() as conn:
        conn.executemany(
            "INSERT INTO earnings (symbol, report_date, session, eps_estimate,"
            " eps_actual, surprise_pct, market_cap, fetched_at) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(symbol, report_date) DO UPDATE SET"
            " session=excluded.session, eps_estimate=excluded.eps_estimate,"
            " eps_actual=excluded.eps_actual, surprise_pct=excluded.surprise_pct,"
            " market_cap=excluded.market_cap, fetched_at=excluded.fetched_at", data)


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
            "SELECT symbol, report_date, session, eps_estimate, eps_actual, surprise_pct,"
            " market_cap, pre_report_close, implied_move_pct, low_liquidity"
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
            "SELECT symbol, report_date, session, eps_estimate, eps_actual, surprise_pct,"
            " market_cap, pre_report_close, implied_move_pct, low_liquidity FROM earnings"
            " WHERE report_date >= ? AND report_date <= ?"
            " ORDER BY report_date", (_et_date(-int(days_back)), _et_date(int(days_fwd))),
        ).fetchall()
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
            "SELECT symbol, report_date, session, eps_estimate, market_cap,"
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


def earnings_reactions_batch(symbols: list[str]) -> dict[str, dict]:
    """Latest reaction data per symbol from the earnings_reactions table."""
    if not symbols:
        return {}
    syms = sorted({s.upper() for s in symbols})
    ph = ",".join("?" for _ in syms)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT symbol, reaction_total, reaction_drift, direction, entry_price"
            f" FROM earnings_reactions WHERE symbol IN ({ph})"
            " ORDER BY ts DESC", syms,
        ).fetchall()
    out = {}
    for r in rows:
        s = r["symbol"].upper()
        if s not in out:
            out[s] = {"reaction_total": r["reaction_total"],
                      "reaction_drift": r["reaction_drift"],
                      "direction": r["direction"]}
    return out
    """The most recent report for `symbol` within `days` (if it has one) — used at
    brief time to decide whether to web-read the report."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT symbol, report_date, session, eps_estimate, eps_actual, surprise_pct"
            " FROM earnings WHERE symbol=? AND eps_actual IS NOT NULL"
            "   AND report_date >= ? AND report_date <= ?"
            " ORDER BY report_date DESC LIMIT 1", (symbol.upper(), _et_date(-int(days)), _et_date(0)),
        ).fetchone()
    return dict(row) if row else None


def news_health() -> dict:
    """Is the news/screener pipeline alive? Last article ingested, how many
    today, and today's AI spend — the one thing on the terminal that still
    runs unattended and can silently fail (Polygon or DeepSeek outage)."""
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
