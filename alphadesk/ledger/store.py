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
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _lock, _connect() as conn:
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


def record_pick(row: dict[str, Any]) -> int:
    row = dict(row)
    row.setdefault("ts", _now())
    for field in _JSON_FIELDS:
        if field in row and not isinstance(row[field], (str, type(None))):
            row[field] = json.dumps(row[field])
    _check_cols(row)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with _lock, _connect() as conn:
        cur = conn.execute(f"INSERT INTO picks ({cols}) VALUES ({marks})", list(row.values()))
        return int(cur.lastrowid or 0)


def update_pick(pick_id: int, **fields: Any) -> None:
    for field in _JSON_FIELDS:
        if field in fields and not isinstance(fields[field], (str, type(None))):
            fields[field] = json.dumps(fields[field])
    _check_cols(fields)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE picks SET {sets} WHERE id = ?", (*fields.values(), pick_id))


def due_for_grading(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM picks WHERE graded_at IS NULL ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
    return [_decode(dict(r)) for r in rows]


def get_graded_exits(days: int = 30) -> list[dict]:
    """Recently graded picks with exit info — for exit param optimization."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, symbol, direction, alpha_net, exit_ts, exit_reason, exit_price,"
            " plan_target, plan_stop, plan_entry"
            " FROM picks WHERE graded_at IS NOT NULL AND exit_ts IS NOT NULL"
            " AND graded_at >= datetime('now', ?)"
            " ORDER BY graded_at DESC LIMIT 200", (f"-{int(days)} days",),
        ).fetchall()
    return [_decode(dict(r)) for r in rows]


def _decode(row: dict) -> dict:
    for field in _JSON_FIELDS:
        if row.get(field):
            try:
                row[field] = json.loads(row[field])
            except Exception:
                pass
    return row


def get_pick(pick_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone()
    return _decode(dict(row)) if row else None


# ---------------------------------------------------------------------------
# Reaction-gate shadow A/B (earnings_reactions)
# ---------------------------------------------------------------------------

def record_reaction(row: dict[str, Any]) -> None:
    """Log one public reporter's reaction for the gate A/B — passed OR dropped. First
    sighting wins (ON CONFLICT IGNORE), so ts anchors the Model-A entry; no LLM cost."""
    row = dict(row)
    row.setdefault("ts", _now())
    _check_cols(row)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with _lock, _connect() as conn:
        conn.execute(
            f"INSERT OR IGNORE INTO earnings_reactions ({cols}) VALUES ({marks})",
            list(row.values()))


def due_reactions(limit: int = 500) -> list[dict]:
    """Reactions whose forward horizon has elapsed and aren't graded yet."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM earnings_reactions WHERE graded_at IS NULL"
            "  AND datetime(ts, '+' || (horizon_days + 2) || ' days') <= datetime('now')"
            " ORDER BY id LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def update_reaction(reaction_id: int, **fields: Any) -> None:
    _check_cols(fields)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE earnings_reactions SET {sets} WHERE id = ?",
                     (*fields.values(), reaction_id))


def alpha_comparison() -> list[dict]:
    """Graded TEAM picks with both the SPY-relative alpha_net and the honest
    (beta-adjusted, borrow-aware) alpha_adj — for the `alpha` prototype report that
    shows how much apparent alpha was really beta exposure / unpriced short borrow."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT direction, session, alpha_net, alpha_adj, beta, low_liquidity FROM picks"
            " WHERE arm='TEAM' AND graded_at IS NOT NULL"
            "   AND alpha_net IS NOT NULL AND alpha_adj IS NOT NULL").fetchall()
    return [dict(r) for r in rows]


def reaction_ab_rows() -> list[dict]:
    """Every graded reaction (reaction_total, alpha_net, gate_passed) for the A/B report."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT reaction_total, alpha_net, gate_passed FROM earnings_reactions"
            " WHERE graded_at IS NOT NULL AND alpha_net IS NOT NULL").fetchall()
    return [dict(r) for r in rows]


def picks_today(arm: str | None = None) -> int:
    query = "SELECT count(*) FROM picks WHERE ts >= ?"
    args: list[Any] = [_et_day_start_utc()]
    if arm:
        query += " AND arm = ?"
        args.append(arm)
    with _connect() as conn:
        return int(conn.execute(query, args).fetchone()[0])


def symbol_traces(symbol: str, days: int = 21) -> list[dict]:
    """Miss post-mortem: every team/solo evaluation of this symbol in the
    last `days` — whether it was approved or rejected, with the full transcript.
    Tells us the desk DID look at it and what it concluded."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, arm, edge, direction, horizon_days, score, adjusted_score,"
            " confidence, verdict, approved, triage_reason, thesis, debate, alpha_net"
            " FROM picks WHERE symbol = ? AND ts >= datetime('now', ?) ORDER BY id DESC",
            (symbol.upper(), f"-{int(days)} days"),
        ).fetchall()
    return [dict(r) for r in rows]


def symbol_skips(symbol: str, days: int = 21, scan: int = 500) -> list[dict]:
    """Miss post-mortem: scout skips that NAMED this symbol in the last `days`,
    with the stated reason — the desk saw it as a candidate and passed."""
    sym = symbol.upper()
    out: list[dict] = []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT window_ts, skip_reasons FROM funnel WHERE window_ts >= datetime('now', ?)"
            " ORDER BY id DESC LIMIT ?",
            (f"-{int(days)} days", scan),
        ).fetchall()
    for r in rows:
        try:
            for s in json.loads(r["skip_reasons"] or "[]"):
                if (s.get("symbol") or "").upper() == sym:
                    out.append({"window_ts": r["window_ts"], "reason": s.get("reason", "")})
        except Exception:
            continue
    return out


def symbol_history(symbol: str, limit: int = 5) -> list[dict]:
    """Episodic memory: this symbol's graded track record (real outcomes only —
    voided picks with no alpha are not a track record)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ts, direction, horizon_days, confidence, alpha_net FROM picks"
            " WHERE symbol = ? AND alpha_net IS NOT NULL ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Stats — the scorecard: edge × horizon × confidence-bucket × arm
# ---------------------------------------------------------------------------

def stats() -> dict:
    # 'graded' counts rows with a real OUTCOME (alpha_net), not just a graded_at
    # stamp: voided picks (ungradeable not-taken / delisted) carry the stamp with
    # NULL alpha and must not inflate the sample gates (calibration prior, buckets).
    with _connect() as conn:
        total = dict(conn.execute(
            "SELECT count(*) AS picks, count(alpha_net) AS graded,"
            " round(avg(alpha_net), 3) AS avg_alpha_net,"
            " round(avg(alpha_adj), 3) AS avg_alpha_adj,"   # beta-adjusted + borrow-aware (honest)
            " sum(CASE WHEN alpha_net > 0 THEN 1 ELSE 0 END) AS wins,"
            " round(sum(exit_return_pct), 2) AS total_return_pct,"
            " count(exit_return_pct) AS exited"
            " FROM picks"
        ).fetchone())
        # Effective (cluster-deduped) graded sample: correlated picks (same sector+direction+
        # day cluster) count ONCE, so N isn't inflated by one bet booked as many. Unclustered
        # graded picks each count as themselves.
        eff = conn.execute(
            "SELECT count(DISTINCT cluster) AS clusters,"
            " sum(CASE WHEN cluster IS NULL THEN 1 ELSE 0 END) AS solo"
            " FROM picks WHERE alpha_net IS NOT NULL").fetchone()
        total["effective_graded"] = int(eff["clusters"] or 0) + int(eff["solo"] or 0)
        by = {}
        for dim, expr in (
            ("edge", "edge"),
            ("arm", "arm"),
            ("horizon", "CASE WHEN horizon_days <= 2 THEN '1-2d' WHEN horizon_days <= 5 THEN '3-5d' ELSE '6-10d' END"),
            ("confidence", "CASE WHEN confidence < 50 THEN '<50' WHEN confidence < 70 THEN '50-70' ELSE '70+' END"),
            ("session", "session"),   # PRE|OPEN|AFTER|CLOSED — which market session's calls pay
        ):
            rows = conn.execute(
                f"SELECT {expr} AS bucket, count(*) AS n, count(alpha_net) AS graded,"
                f" round(avg(alpha_net), 3) AS avg_alpha_net,"
                f" sum(CASE WHEN alpha_net > 0 THEN 1 ELSE 0 END) AS wins"
                f" FROM picks GROUP BY bucket"
            ).fetchall()
            by[dim] = [dict(r) for r in rows]
        debate = dict(conn.execute(
            "SELECT round(avg(CASE WHEN"
            " ((adjusted_score > 50) = (alpha_net > 0)) THEN 1.0 ELSE 0.0 END), 3) AS post_debate_acc,"
            " round(avg(CASE WHEN"
            " ((score > 50) = (alpha_net > 0)) THEN 1.0 ELSE 0.0 END), 3) AS pre_debate_acc"
            " FROM picks WHERE arm = 'TEAM' AND alpha_net IS NOT NULL"
        ).fetchone())
    return {"total": total, "by": by, "debate_lift": debate}


# ---------------------------------------------------------------------------
# Funnel + tokens
# ---------------------------------------------------------------------------

def funnel_add(ingested: int, candidates: int, picked: int, skipped: int,
               skip_reasons: list[dict]) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO funnel (window_ts, ingested, candidates, picked, skipped, skip_reasons)"
            " VALUES (?,?,?,?,?,?)",
            (_now(), ingested, candidates, picked, skipped, json.dumps(skip_reasons[:20])),
        )


def token_sink(role: str, model: str, tin: int, tout: int,
               decision_id: str | None, source: str | None = None) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO token_usage (ts, role, model, input_tok, output_tok, decision_id, source)"
            " VALUES (?,?,?,?,?,?,?)", (_now(), role, model, tin, tout, decision_id, source),
        )


def record_ingest(source: str, articles: int, candidates: int) -> None:
    """One row per source per run: articles in → candidates out. Feeds the source
    scorecard's volume column."""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO ingest_stats (ts, source, articles, candidates) VALUES (?,?,?,?)",
            (_now(), source.upper(), int(articles), int(candidates)),
        )


def source_scorecard(days: int = 30) -> list[dict]:
    """Per ingestion source: volume (articles/candidates), cost (ingestion +
    debate tokens), and value (picks/taken/graded/avg alpha). Answers which
    channel earns its tokens. 'shared' bucket = cross-source calls (scout)."""
    since = f"-{int(days)} day"
    with _connect() as conn:
        vol = {r["source"]: dict(r) for r in conn.execute(
            "SELECT source, sum(articles) AS articles, sum(candidates) AS candidates"
            " FROM ingest_stats WHERE ts >= datetime('now', ?) GROUP BY source", (since,))}
        # ingestion tokens: tagged directly on the call
        ing_tok = {r["source"]: r["tok"] for r in conn.execute(
            "SELECT source, sum(input_tok + output_tok) AS tok FROM token_usage"
            " WHERE source IS NOT NULL AND ts >= datetime('now', ?) GROUP BY source", (since,))}
        # debate tokens: attributed via the pick's decision_id → its source
        deb_tok = {r["source"]: r["tok"] for r in conn.execute(
            "SELECT p.source AS source, sum(t.input_tok + t.output_tok) AS tok"
            " FROM token_usage t JOIN picks p ON t.decision_id = p.decision_id"
            " WHERE p.source IS NOT NULL AND t.ts >= datetime('now', ?) GROUP BY p.source", (since,))}
        val = {r["source"]: dict(r) for r in conn.execute(
            "SELECT source, count(*) AS picks, sum(taken) AS taken,"
            " sum(CASE WHEN alpha_net IS NOT NULL THEN 1 ELSE 0 END) AS graded,"
            " round(avg(alpha_net), 2) AS avg_alpha FROM picks"
            " WHERE arm='TEAM' AND source IS NOT NULL AND ts >= datetime('now', ?)"
            " GROUP BY source", (since,))}

    sources = set(vol) | set(ing_tok) | set(deb_tok) | set(val)
    out = []
    for s in sources:
        v, va = vol.get(s, {}), val.get(s, {})
        out.append({
            "source": s,
            "articles": v.get("articles") or 0,
            "candidates": v.get("candidates") or 0,
            "ingest_tokens": ing_tok.get(s) or 0,
            "debate_tokens": deb_tok.get(s) or 0,
            "tokens": (ing_tok.get(s) or 0) + (deb_tok.get(s) or 0),
            "picks": va.get("picks") or 0,
            "taken": va.get("taken") or 0,
            "graded": va.get("graded") or 0,
            "avg_alpha": va.get("avg_alpha"),
        })
    out.sort(key=lambda r: -r["tokens"])
    return out


def token_summary(days: int = 1) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, model, count(*) AS calls, sum(input_tok) AS input_tok,"
            " sum(output_tok) AS output_tok FROM token_usage"
            f" WHERE ts >= datetime('now', '-{int(days)} day') GROUP BY role, model"
            " ORDER BY output_tok DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def install_token_sink() -> None:
    pass


def save_relationship(from_sym: str, to_sym: str, direction: str, chain: str) -> None:
    pass
    """Cache a web-verified ripple relationship (the graph-lite grows on use)."""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO relationships (ts, from_sym, to_sym, direction, chain)"
            " VALUES (?,?,?,?,?)",
            (_now(), from_sym.upper(), to_sym.upper(), direction, chain),
        )


def get_relationships(from_sym: str, days: int = 7) -> list[dict]:
    """Pre-search cache: ripple neighbors mapped for this shocked company within
    the last `days`. Lets the Connections desk reuse a prior web-verified mapping
    instead of re-running the web specialists for the same shock."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT to_sym, direction, chain, max(ts) AS ts FROM relationships"
            " WHERE from_sym = ? AND ts >= datetime('now', ?)"
            " GROUP BY to_sym, direction ORDER BY ts DESC",
            (from_sym.upper(), f"-{int(days)} days"),
        ).fetchall()
    return [dict(r) for r in rows]


def save_relation_facts(rows: list[dict]) -> int:
    """Persist news-stated relations (from_sym -rel-> to_sym, evidence URL).
    First sighting inserts; repeats just refresh last_seen. Returns new inserts."""
    if not rows:
        return 0
    now = _now()
    n = 0
    with _lock, _connect() as conn:
        for r in rows:
            a, b, rel = (r.get("from_sym") or "").upper(), (r.get("to_sym") or "").upper(), r.get("rel")
            if not (a and b and rel):
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO relation_facts"
                " (first_seen, last_seen, from_sym, to_sym, rel, evidence)"
                " VALUES (?,?,?,?,?,?)",
                (now, now, a, b, rel, (r.get("evidence") or "")[:300]))
            n += cur.rowcount or 0
            conn.execute(
                "UPDATE relation_facts SET last_seen=? WHERE from_sym=? AND to_sym=? AND rel=?",
                (now, a, b, rel))
    return n


def get_relation_facts(symbol: str) -> list[dict]:
    """All news-stated relations touching `symbol` (either side), freshest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT from_sym, to_sym, rel, evidence, last_seen FROM relation_facts"
            " WHERE from_sym = ? OR to_sym = ? ORDER BY last_seen DESC LIMIT 30",
            (symbol.upper(), symbol.upper()),
        ).fetchall()
    return [dict(r) for r in rows]


def last_debate(symbol: str) -> dict | None:
    """The most recent team debate for `symbol` (ts + what it was about) — so a
    later run can tell 'same story' from a genuinely new catalyst."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT ts, triage_reason, thesis, exit_reason, exit_ts, entry_price, session"
            " FROM picks WHERE arm='TEAM' AND symbol=?"
            " ORDER BY id DESC LIMIT 1", (symbol.upper(),),
        ).fetchone()
    return dict(row) if row else None


def symbols_debated_since(hours: int = 12) -> set:
    """Symbols with a team debate in the last `hours` — skip re-debating them
    (anti-double-dip: an earnings/news name lingers as a candidate for days)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM picks WHERE arm IN ('TEAM','QUANT')"
            " AND ts >= datetime('now', ?)", (f"-{int(hours)} hours",),
        ).fetchall()
    return {r["symbol"].upper() for r in rows}


def mark_taken(pick_ids: list[int]) -> None:
    """Flag the picks the Chief chose to TAKE — the open positions later runs re-check."""
    if not pick_ids:
        return
    with _lock, _connect() as conn:
        conn.executemany("UPDATE picks SET taken=1 WHERE id=?", [(int(i),) for i in pick_ids])


def open_taken_picks() -> list[dict]:
    """TAKE picks still within their horizon, not exited, not yet graded — the
    open positions a fresh run should re-evaluate ('are you still in this trade?')."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, symbol, direction, horizon_days, adjusted_score, confidence,"
            " edge, thesis, session, entry_price, spy_price, plan_entry, plan_target, plan_stop,"
            " triage_reason, low_liquidity, mfe_pct, broker_order_id, broker_status,"
            " hedge_of, arm FROM picks"
            " WHERE taken=1 AND exit_ts IS NULL AND graded_at IS NULL"
            "   AND datetime(ts, '+' || (horizon_days + 3) || ' days') >= datetime('now')"
            " ORDER BY id DESC",
        ).fetchall()
    return [dict(r) for r in rows]


def set_broker_order(pick_id: int, order_id: str | None, status: str,
                     qty: float = 0.0) -> None:
    """Stamp the paper-broker (Alpaca) order state on a pick — so the reconciler knows what
    it has already routed and never double-submits."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE picks SET broker_order_id=?, broker_status=?, broker_qty=? WHERE id=?",
            (order_id, status[:200], float(qty), int(pick_id)))


def set_broker_fill(pick_id: int, price: float, ts: str) -> None:
    """Stamp the broker's ACTUAL fill — the ledger's honest entry for this pick
    (grader prefers it over the Model-A open fill when present)."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE picks SET broker_fill_price=?, broker_fill_ts=? WHERE id=?",
            (round(float(price), 4), str(ts)[:40], int(pick_id)))


def picks_with_open_broker_orders() -> list[dict]:
    """Routed-but-unfilled picks (broker_order_id set, order not terminal, not
    exited) — the set the paper PM reconciles against."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, symbol, direction, broker_order_id, broker_status FROM picks"
            " WHERE broker_order_id IS NOT NULL AND exit_ts IS NULL"
            " AND COALESCE(broker_status, '') NOT IN ('filled','cancelled','expired','rejected')"
            " ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def pm_managed_symbols() -> set[str]:
    """Symbols the paper PM has ever routed to the broker — so reconcile() only ever
    CLOSES positions it opened itself, never a manual trade in the same account."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM picks WHERE broker_order_id IS NOT NULL"
        ).fetchall()
    return {r["symbol"].upper() for r in rows}


def open_hedge_for(parent_id: int) -> dict | None:
    """The still-open hedge (if any) protecting a parent position. None if no hedge
    exists or it was already closed."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM picks WHERE hedge_of=? AND exit_ts IS NULL AND graded_at IS NULL LIMIT 1",
            (int(parent_id),),
        ).fetchone()
    return dict(row) if row else None


def open_hedges() -> list[dict]:
    """All still-open hedges — for the watcher to monitor and close when parents exit."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM picks WHERE hedge_of IS NOT NULL AND exit_ts IS NULL"
            " AND graded_at IS NULL ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def recent_team_picks(days: int = 30) -> list[dict]:
    """All TEAM/QUANT picks in the window, for per-symbol timelines (stance changes +
    outcomes). Ordered so grouping keeps each symbol's events in time order."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, symbol, direction, horizon_days, edge, verdict, approved,"
            " adjusted_score, confidence, session, plan_entry, plan_target, plan_stop, plan_note,"
            " entry_price, spy_price, alpha_net, alpha_adj, beta, ret_horizon, graded_at, exit_ts, exit_reason,"
            " exit_price, exit_return_pct, exit_alpha, mfe_pct, mae_pct, taken"
            " FROM picks WHERE arm IN ('TEAM','QUANT') AND ts >= datetime('now', ?)"
            " ORDER BY symbol, id", (f"-{int(days)} days",),
        ).fetchall()
    return [dict(r) for r in rows]


def picks_for_path(days: int = 20) -> list[dict]:
    """Positions to (re)compute MFE/MAE for: carry a plan, recent, and either
    still open (running peak/trough) or closed but not yet path-graded. Idempotent
    and bounded — open ones update each pass, closed ones compute once."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, symbol, direction, horizon_days, session, entry_price, exit_price,"
            " low_liquidity, exit_ts, plan_entry, order_type, mfe_pct FROM picks"
            " WHERE arm IN ('TEAM','QUANT') AND plan_entry IS NOT NULL"
            "   AND ts >= datetime('now', ?)"
            "   AND (mfe_pct IS NULL OR (graded_at IS NULL AND exit_ts IS NULL))",
            (f"-{int(days)} days",),
        ).fetchall()
    return [dict(r) for r in rows]


def live_picks() -> list[dict]:
    """Open TAKEN picks carrying a trade plan, still inside their horizon window
    (not graded, not exited) — the set to track live against the current price.
    taken=0 picks (counterfactuals the Head passed on or the concentration cap held
    back) are excluded — they are not real positions and can't be live-monitored."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, symbol, direction, horizon_days, session, edge, verdict,"
            " approved, adjusted_score, confidence, taken, spy_price, entry_price,"
            " plan_entry, plan_target, plan_stop, plan_note, thesis, triage_reason,"
            " order_type, mfe_pct, low_liquidity, broker_order_id, broker_fill_price,"
            " broker_qty, hedge_of, arm"
            " FROM picks"
             " WHERE arm IN ('TEAM','QUANT','HEDGE') AND plan_entry IS NOT NULL"
            "   AND taken = 1"
            "   AND graded_at IS NULL AND exit_ts IS NULL"
            "   AND datetime(ts, '+' || (horizon_days + 2) || ' days') >= datetime('now')"
            " ORDER BY approved DESC, id DESC",
        ).fetchall()
    return [dict(r) for r in rows]


def delete_picks(ids: list[int]) -> int:
    """Hard-delete picks by id — used to roll back an interrupted Find Trades run so
    a fresh run isn't blocked (or duplicated) by its abandoned in-progress picks."""
    ids = [int(i) for i in ids if i]
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    with _lock, _connect() as conn:
        cur = conn.execute(f"DELETE FROM picks WHERE id IN ({marks})", ids)
        return cur.rowcount


def set_entry_price(pick_id: int, price: float) -> None:
    """Stamp the actual FILL price on a closed-market pick once its 9:30 open has
    passed (Model A). Only fills a still-NULL entry_price, so live P&L / exits and
    the grade all measure from the same real open price."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE picks SET entry_price=? WHERE id=? AND entry_price IS NULL",
            (round(float(price), 4), int(pick_id)))


def record_exit(pick_id: int, reason: str, exit_price: float | None = None,
                exit_return_pct: float | None = None,
                exit_alpha: float | None = None) -> bool:
    """Stamp an early exit (a target/stop hit, a session-close, or a review)
    WITH its realized performance at the exit price.

    Session-scoped model: a position's result IS its exit (it never carries past
    its session), so a real exit with an alpha also becomes the pick's grade —
    alpha_net = the realized exit alpha vs SPY, ret_horizon = the realized return,
    and graded_at is stamped so the forward 1-day grader never re-grades it.
    (COALESCE keeps an existing grade if one somehow already resolved.)

    Idempotent: the `exit_ts IS NULL` guard means only the FIRST close wins — three
    writers (watcher level-cross, run review, watcher escalation) can race the same
    open position, and the guard stops a second one overwriting the realized price/
    reason (or, with real orders, sending a second close). Returns True if this call
    closed it, False if it was already closed."""
    graded = datetime.now(timezone.utc).isoformat() if exit_alpha is not None else None
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE picks SET exit_ts=?, exit_reason=?, exit_price=?,"
            " exit_return_pct=?, exit_alpha=?,"
            " alpha_net=COALESCE(alpha_net, ?), ret_horizon=COALESCE(ret_horizon, ?),"
            " graded_at=COALESCE(graded_at, ?)"
            " WHERE id=? AND exit_ts IS NULL",
            (_now(), reason, exit_price, exit_return_pct, exit_alpha,
             exit_alpha, exit_return_pct, graded, int(pick_id)))
        return cur.rowcount > 0


def open_position_count() -> int:
    """Live open TAKEN positions (not exited, not graded, within window)."""
    with _connect() as conn:
        return int(conn.execute(
            "SELECT count(*) FROM picks WHERE taken=1 AND exit_ts IS NULL"
            " AND graded_at IS NULL"
            " AND datetime(ts, '+' || (horizon_days + 2) || ' days') >= datetime('now')"
        ).fetchone()[0])


def today_realized_pnl_pct() -> float:
    """Sum of realized exit returns today (equal-weight) — the daily-loss circuit breaker."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(sum(exit_return_pct), 0) FROM picks"
            " WHERE exit_ts IS NOT NULL AND exit_ts >= ?", (_et_day_start_utc(),)).fetchone()
        return float(row[0] or 0)


def today_exit_stats() -> dict:
    """Today's realized exits: total equal-weight P&L, count, and the per-session split."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT session, exit_return_pct FROM picks"
            " WHERE exit_ts IS NOT NULL AND exit_ts >= ?", (_et_day_start_utc(),)).fetchall()
    total = 0.0
    per: dict[str, float] = {}
    for r in rows:
        total += r["exit_return_pct"] or 0
        s = r["session"] or "?"
        per[s] = per.get(s, 0.0) + (r["exit_return_pct"] or 0)
    return {"total": round(total, 2), "n": len(rows),
            "per_session": {k: round(v, 2) for k, v in per.items()}}


def cluster_take_count(cluster: str) -> int:
    """How many TAKEN picks today share this (sector|direction) cluster."""
    with _connect() as conn:
        return int(conn.execute(
            "SELECT count(*) FROM picks WHERE taken=1 AND cluster=? AND ts >= ?",
            (cluster, _et_day_start_utc())).fetchone()[0])


def set_cluster(pick_id: int, cluster: str | None) -> None:
    with _lock, _connect() as conn:
        conn.execute("UPDATE picks SET cluster=? WHERE id=?", (cluster, int(pick_id)))


def cached_daily(symbols: list[str], start: str, end: str) -> dict[str, list[dict]]:
    """Daily OHLC rows per symbol in [start, end] (ISO dates), from the local cache."""
    if not symbols:
        return {}
    syms = sorted({s.upper() for s in symbols})
    ph = ",".join("?" for _ in syms)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT symbol, date, open, high, low, close, volume FROM price_daily"
            f" WHERE symbol IN ({ph}) AND date >= ? AND date <= ?",
            (*syms, start, end)).fetchall()
    out = {s: [] for s in syms}
    for r in rows:
        out.setdefault(r["symbol"], []).append(dict(r))
    return out


def save_cached_daily(rows: list[dict]) -> int:
    """Insert/replace daily OHLC rows into the local cache. Returns rows saved."""
    data = [(r["symbol"].upper(), r["date"], r.get("open"), r.get("high"), r.get("low"),
             r.get("close"), r.get("volume"))
            for r in rows if r.get("symbol") and r.get("date")]
    if not data:
        return 0
    with _lock, _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO price_daily (symbol, date, open, high, low, close, volume)"
            " VALUES (?,?,?,?,?,?,?)", data)
    return len(data)


def cached_daily_span(symbol: str) -> tuple[str, str] | None:
    """(min_date, max_date) cached for a symbol, or None if nothing cached."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT min(date) AS lo, max(date) AS hi FROM price_daily WHERE symbol=?",
            (symbol.upper(),)).fetchone()
    return (row["lo"], row["hi"]) if row and row["lo"] else None


def record_skips(skips: list[dict], cap: int = 30) -> None:
    """Persist skipped candidates individually so their forward moves can be graded
    (anti-survivorship: did we pass on a name that then moved big?). Capped per
    window to bound later grading cost, and DEDUPED per symbol per day — the quant
    pipeline re-evaluates the same candidates every run, so only the first skip of
    the day is kept (the reason closest to the event)."""
    if not skips:
        return
    day_start = _et_day_start_utc()
    with _lock, _connect() as conn:
        seen = {r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM skips WHERE ts >= ?", (day_start,))}
        rows = []
        for s in skips[:cap]:
            sym = (s.get("symbol") or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            rows.append((_now(), sym, (s.get("reason") or "")[:200]))
        if rows:
            conn.executemany("INSERT INTO skips (ts, symbol, reason) VALUES (?,?,?)", rows)


def due_skips(limit: int = 300) -> list[dict]:
    """Ungraded skips (the grader filters by whether the window has elapsed)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM skips WHERE graded_at IS NULL ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_skip(skip_id: int, **fields: Any) -> None:
    if not fields:
        return
    _check_cols(fields)
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE skips SET {cols} WHERE id=?", (*fields.values(), int(skip_id)))


def false_negative_stats() -> dict:
    """The survivorship scorecard: how often the desk was wrong to say NO.
    - reject: graded TEAM calls the desk did NOT BOOK (taken=0) that would have
      beaten SPY (alpha_net > 0 in the proposed direction — a passed-over winner).
      Keyed on taken, NOT approved: in TAKE-ALL mode approved=0 picks are still
      booked as positions, so counting them here would double-count outcomes
      already in the main scorecard and tell the desk it 'passed on' winners it
      actually holds. taken=0 is the true counterfactual set (pre-take-all
      non-approvals + concentration-capped picks).
    - skip:   graded scout skips that made a big move we never looked at."""
    with _connect() as conn:
        rej = dict(conn.execute(
            "SELECT count(*) AS graded,"
            " sum(CASE WHEN alpha_net > 0 THEN 1 ELSE 0 END) AS missed"
            " FROM picks WHERE arm='TEAM' AND taken=0 AND alpha_net IS NOT NULL"
        ).fetchone())
        skp = dict(conn.execute(
            "SELECT count(*) AS graded, sum(CASE WHEN missed=1 THEN 1 ELSE 0 END) AS missed"
            " FROM skips WHERE graded_at IS NOT NULL"
        ).fetchone())
    return {"reject": rej, "skip": skp}


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
    """Insert/replace earnings-calendar rows. Each: {symbol, report_date, session,
    eps_estimate, eps_actual, surprise_pct, market_cap}."""
    data = [(r["symbol"].upper(), r["report_date"], r.get("session"),
             r.get("eps_estimate"), r.get("eps_actual"), r.get("surprise_pct"),
             r.get("market_cap"), _now())
            for r in (rows or []) if r.get("symbol") and r.get("report_date")]
    if not data:
        return
    with _lock, _connect() as conn:
        conn.executemany(
            "INSERT INTO earnings (symbol, report_date, session, eps_estimate,"
            " eps_actual, surprise_pct, market_cap, fetched_at) VALUES (?,?,?,?,?,?,?,?)", data)


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
            " market_cap, pre_report_close, implied_move_pct"
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


def earnings_engagement(symbols: list[str], days_back: int = 6) -> dict[str, dict]:
    if not symbols:
        return {}
    syms = sorted({s.upper() for s in symbols})
    ph = ",".join("?" for _ in syms)
    with _connect() as conn:
        picks = conn.execute(
            f"SELECT symbol, id, direction, taken, alpha_net, verdict, thesis, debate, ts"
            f" FROM picks WHERE arm IN ('TEAM','QUANT') AND symbol IN ({ph}) AND ts >= datetime('now', ?)"
            " ORDER BY ts DESC", (*syms, f"-{int(days_back)} days"),
        ).fetchall()
        skips = conn.execute(
            f"SELECT symbol, reason, ts FROM skips WHERE symbol IN ({ph})"
            " AND ts >= datetime('now', ?) ORDER BY ts DESC",
            (*syms, f"-{int(days_back)} days"),
        ).fetchall()
    out: dict[str, dict] = {}
    for r in picks:
        s = r["symbol"].upper()
        if s in out:
            continue
        why = ""
        try:
            why = (json.loads(r["debate"] or "{}") or {}).get("arbiter_summary") or ""
        except (ValueError, TypeError):
            why = ""
        why = (why or r["thesis"] or "").strip()[:500]
        out[s] = {
            "state": "TOOK" if r["taken"] else "DEBATED", "ts": r["ts"],
            "direction": r["direction"], "pick_id": r["id"], "verdict": r["verdict"],
            "alpha_net": r["alpha_net"], "why": why}
    for r in skips:                         # only if the desk never debated it
        s = r["symbol"].upper()
        out.setdefault(s, {"state": "SKIPPED", "ts": r["ts"], "why": (r["reason"] or "").strip()})
    return out


def earnings_window(days_back: int = 4, days_fwd: int = 14) -> list[dict]:
    """All calendar rows in [today-days_back, today+days_fwd] — reported AND
    upcoming, NOT gated on eps_actual. For the time-aware Calendar view, which
    splits reported/upcoming by when the report is public (see earnings.reported_public),
    not by whether Nasdaq has backfilled the actual EPS yet."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, report_date, session, eps_estimate, eps_actual, surprise_pct,"
            " market_cap, pre_report_close, implied_move_pct FROM earnings"
            " WHERE report_date >= ? AND report_date <= ?"
            " ORDER BY report_date", (_et_date(-int(days_back)), _et_date(int(days_fwd))),
        ).fetchall()
    return [dict(r) for r in rows]


def upcoming_earnings(days: int = 7) -> list[dict]:
    """Companies REPORTING in the next `days` — the 'be ready' watch."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, report_date, session, eps_estimate, market_cap,"
            " pre_report_close, implied_move_pct FROM earnings"
            " WHERE eps_actual IS NULL AND report_date >= ?"
            "   AND report_date <= ? ORDER BY report_date", (_et_date(0), _et_date(int(days))),
        ).fetchall()
    return [dict(r) for r in rows]


def earnings_row(symbol: str, days: int = 4) -> dict | None:
    """The most recent report for `symbol` within `days` (if it has one)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT symbol, report_date, session, eps_estimate, eps_actual, surprise_pct"
            " FROM earnings WHERE symbol=? AND eps_actual IS NOT NULL"
            "   AND report_date >= ? AND report_date <= ?"
            " ORDER BY report_date DESC LIMIT 1", (symbol.upper(), _et_date(-int(days)), _et_date(0)),
        ).fetchone()
    return dict(row) if row else None


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


def add_run(kind: str, top_picks: list[dict]) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT INTO runs (ts, kind, top_picks) VALUES (?,?,?)",
                     (_now(), kind, json.dumps(top_picks)))


def last_run_time(kind: str = "FIND_TRADES") -> str | None:
    """ISO ts of the most recent run of `kind`, or None — so the auto-run's interval gate
    survives restarts (won't re-fire inside the interval after a crash/deploy)."""
    with _connect() as conn:
        row = conn.execute("SELECT ts FROM runs WHERE kind = ? ORDER BY id DESC LIMIT 1",
                           (kind,)).fetchone()
    return row["ts"] if row else None


def runs_today(kind: str = "FIND_TRADES") -> int:
    """Count of runs of `kind` recorded so far today (ET) — the durable half of the
    daily runaway cap, so a restart can't zero an in-memory counter and bypass it."""
    from alphadesk.config import now_et
    start = now_et().replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start.astimezone(timezone.utc).isoformat()
    with _connect() as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE kind = ? AND ts >= ?",
            (kind, start_utc)).fetchone()
    return int(n)


def performance_rows(days: int = 30) -> list[dict]:
    """Exited picks with everything the performance page needs — realized P&L,
    alpha, path (MFE/MAE), plan levels, and the quant signals that fired."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, symbol, direction, session, horizon_days,"
            " exit_ts, exit_return_pct, exit_alpha, alpha_net, mfe_pct, mae_pct,"
            " plan_entry, plan_target, plan_stop, entry_price, exit_price,"
            " score, adjusted_score, thesis, debate"
            " FROM picks WHERE exit_ts IS NOT NULL AND exit_return_pct IS NOT NULL"
            "   AND ts >= datetime('now', ?) ORDER BY exit_ts",
            (f"-{int(days)} days",)).fetchall()
    return [_decode(dict(r)) for r in rows]


def runs_summary_today(kind: str = "FIND_TRADES") -> dict:
    """Run-activity for the system-health panel: total runs today, how many actually
    booked picks (top_picks non-empty), and the most recent run time."""
    start = _et_day_start_utc()
    with _connect() as conn:
        total = int(conn.execute(
            "SELECT count(*) FROM runs WHERE kind=? AND ts >= ?", (kind, start)).fetchone()[0])
        with_picks = int(conn.execute(
            "SELECT count(*) FROM runs WHERE kind=? AND ts >= ? AND top_picks NOT IN ('[]','')",
            (kind, start)).fetchone()[0])
        last = conn.execute(
            "SELECT max(ts) FROM runs WHERE kind=?", (kind,)).fetchone()[0]
    return {"total": total, "with_picks": with_picks, "last_ts": last}


def funnel_today() -> dict:
    """Today's coverage funnel: candidates scored → picked → why-dropped."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(sum(candidates),0) AS candidates,"
            " COALESCE(sum(picked),0) AS picked, COALESCE(sum(skipped),0) AS skipped"
            " FROM funnel WHERE window_ts >= ?", (_et_day_start_utc(),)).fetchone()
    return dict(row) if row else {"candidates": 0, "picked": 0, "skipped": 0}


init()
