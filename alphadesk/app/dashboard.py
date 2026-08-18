"""The terminal's HTTP surface — JSON API + the built SPA. No auth.

AlphaDesk is a consumption product: every endpoint here READS. Nothing books,
holds, closes or scores a position. The trading endpoints (/api/picks/*,
/api/live, /api/performance, /api/sessions, /api/timelines, /api/stats,
/api/sources, /api/quant/stats) were removed with the execution layer on
2026-08-18.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from alphadesk.ledger import store

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="AlphaDesk")


@app.middleware("http")
async def _passthrough(request: Request, call_next):
    return await call_next(request)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness for the GCP uptime check: 200 while the ingest loop is
    cycling, 503 if it has been silent >30 min (hung loop / dead scheduler).
    First 30 min after boot count as healthy (startup grace)."""
    from alphadesk.app import scheduler
    age = scheduler.heartbeat_age_s()
    if age < 1800 or age == float("inf") and _process_age_s() < 1800:
        return {"ok": True}
    if age == float("inf"):
        return Response("scheduler never ticked", status_code=503)
    return Response(f"ingest silent {int(age)}s", status_code=503)


_BOOT_MONO = __import__("time").monotonic()


def _process_age_s() -> float:
    import time
    return time.monotonic() - _BOOT_MONO


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@app.get("/api/filings/{symbol}")
def api_filings_list(symbol: str):
    """A symbol's recent 10-K/10-Q/8-K filings, straight from EDGAR (cheap —
    one JSON fetch, cached into the filings table). Never 500s on an unknown
    symbol or an EDGAR hiccup — returns an empty list, which the UI renders
    as 'no filings found', not an error."""
    from alphadesk.desk import filings
    sym = "".join(c for c in symbol.upper() if c.isalnum() or c in ".-")[:12]
    if not sym:
        raise HTTPException(400, "bad symbol")
    return {"symbol": sym, "filings": filings.list_filings(sym)}


class FilingQuestion(BaseModel):
    accession: str
    question: str


@app.post("/api/filings/ask")
def api_filings_ask(body: FilingQuestion):
    """Answer a question about ONE filing, backed only by verbatim quotes
    verified against the actual SEC document text — see desk/filings.py's
    module docstring for why this is a stronger guarantee than the
    screener's index-based citations. Cached per (accession, question); a
    repeat ask is free."""
    from alphadesk.desk import filings
    accession = body.accession.strip()
    question = body.question.strip()
    if not accession or not question:
        raise HTTPException(400, "accession and question are required")
    result = filings.ask(accession, question)
    if result is None:
        raise HTTPException(
            422, "couldn't answer — the filing text wasn't available or the AI call failed")
    return result


class ResearchQuestion(BaseModel):
    symbol: str
    question: str


@app.post("/api/research/ask")
def api_research_ask(body: ResearchQuestion):
    """Ask a question about one symbol, answered from its pre-fetched
    fundamentals/ownership/insider/earnings/macro/sector data in a single AI
    call — every claim is tied to a real, server-fetched data section, never
    the model's unverified say-so — see desk/research.py's module docstring.
    Cached per (symbol, question) with a TTL (the underlying data can go
    stale even when the question hasn't changed)."""
    from alphadesk.desk import research
    sym = "".join(c for c in body.symbol.upper() if c.isalnum() or c in ".-")[:12]
    question = body.question.strip()
    if not sym or not question:
        raise HTTPException(400, "symbol and question are required")
    result = research.ask(sym, question)
    if result is None:
        raise HTTPException(
            422, "couldn't answer — no usable data for this symbol or the AI call failed")
    return result


@app.get("/api/screener")
def api_screener():
    """Everything in the current window, UNRANKED and alphabetical — symbols
    with fresh news or a report inside SCREENER_HORIZON_DAYS, each with its
    raw headlines. Pure database read: no LLM call, no score, no top-N. The
    order of this list is not a recommendation (see desk/screener.py)."""
    from alphadesk.desk import screener
    return {"symbols": screener.inventory()}


class ScreenerQuestion(BaseModel):
    question: str


@app.post("/api/screener/ask")
def api_screener_ask(body: ScreenerQuestion):
    """Ask one question of the WHOLE window at once — every article and
    upcoming report across every symbol, in a single AI call. This is the
    only place the screener spends tokens: nothing is narrated in the
    background, so the model never pre-decides what was interesting.

    Every claim cites a numbered item resolved server-side back to the stored
    article or calendar row (desk/screener._resolve_citations)."""
    from alphadesk.desk import screener
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question is required")
    result = screener.ask(question)
    if result is None:
        raise HTTPException(
            422, "couldn't answer — nothing in the current window or the AI call failed")
    return result


@app.get("/api/chart/{symbol}")
def api_chart(symbol: str, days: int = 2):
    """OHLC + RSI-9 + MACD(12,26,9) series for the human decision chart.

    Always returns the data-quality block (coverage / median_gap_min /
    indicators_reliable). The UI must render that: on the free IEX feed an
    illiquid name's "1-minute" chart can be a handful of prints stretched
    across days, and it draws identically to a real one.
    """
    from alphadesk.ingest import prices
    sym = "".join(c for c in symbol.upper() if c.isalnum() or c in ".-")[:12]
    if not sym:
        raise HTTPException(400, "bad symbol")
    series = prices.get_chart_series(sym, days=days)
    if not series:
        raise HTTPException(404, f"no intraday bars for {sym}")
    return series


@app.get("/api/tokens")
def api_tokens(days: int = 1):
    days = max(1, min(days, 365))   # a negative `days` becomes an invalid SQLite modifier → NULL → misleading data
    return {"days": days, "usage": store.token_summary(days)}


@app.get("/api/system")
def api_system():
    """Is the terminal alive and is data still arriving?

    Deliberately narrow: uptime, the market session, and the health of the one
    thing that runs unattended (the news/AI pipeline). The old position and
    grading counters went with the execution layer — a consumption product has
    no positions to report."""
    from alphadesk.config import session as market_session
    return {
        "uptime_s": round(_process_age_s()),
        "market": market_session(),
        "news": store.news_health(),
    }


@app.get("/api/earnings")
def api_earnings():
    """Be-ready view: who reports next (with the time to RUN the desk to catch the
    drift) and who just reported."""
    from alphadesk.config import now_et
    from alphadesk.ingest.earnings import reported_public, run_at

    # Time-aware split: a report is "just reported" once it's PUBLIC (BMO/DAY at the
    # 9:30 open, AMC at the 16:00 close of its report day) — not when Nasdaq happens
    # to backfill the actual EPS. So a name reporting today flips after 9:30 today.
    now = now_et()
    upcoming, reported = [], []
    for e in store.earnings_window(days_back=4, days_fwd=14):
        pub = reported_public(e["report_date"])
        e["public_at"] = pub.isoformat() if pub else None   # when the report becomes tradeable (BMO/DAY 4:00, AMC 16:00 ET)
        if pub is not None and now >= pub:
            reported.append(e)
        else:
            e["run_at"] = run_at(e["report_date"], e.get("session"))
            upcoming.append(e)
    # Sort so the UI can group by run-day (earliest to run first) with the biggest
    # names surfaced first inside each day — never truncated by earlier small-caps.
    upcoming.sort(key=lambda e: (e["run_at"] or "9999", -(e.get("market_cap") or 0.0)))
    # newest report first, then group by report-day in the UI (biggest names first)
    # reverse=True → newest report day first AND biggest market cap first within a
    # day (a plain cap here, NOT -cap: reverse already flips it to descending).
    reported.sort(key=lambda e: (e["report_date"], e.get("market_cap") or 0.0), reverse=True)

    # Collapse dual-class listings of the same company (identical report date +
    # market cap to the dollar, e.g. GOOG/GOOGL) to one row. Two different firms
    # never share a 13-digit cap exactly, so this only merges share classes.
    def _dedupe_dual(rows: list[dict]) -> list[dict]:
        seen: set = set()
        out = []
        for e in rows:
            mc = e.get("market_cap")
            if mc:
                key = (e["report_date"], mc)
                if key in seen:
                    continue
                seen.add(key)
            out.append(e)
        return out

    # Sort so the UI can group by run-day (earliest to run first) with the biggest
    # names surfaced first inside each day — never truncated by earlier small-caps.
    upcoming.sort(key=lambda e: (e["run_at"] or "9999", -(e.get("market_cap") or 0.0)))
    upcoming = _dedupe_dual(upcoming)
    reported = _dedupe_dual(reported)
    # How each name actually MOVED after reporting is consumption data and
    # stays. Whether "the desk engaged" with it was a measurement concept and
    # went with the ledger.
    reactions = store.earnings_reactions_batch([e["symbol"] for e in reported])
    for e in reported:
        r = reactions.get(e["symbol"].upper())
        if r:
            e["move_since_report_pct"] = r["reaction_total"]
            # The honest miss gauge is the CAPTURABLE drift from the first post-report
            # open — a gap move (total big, drift ~0) is not a tradeable miss. When no
            # regular session had traded at sighting, drift is NULL and the whole move
            # is extended-hours (capturable) → fall back to the total.
            e["move_drift_pct"] = r.get("reaction_drift") if r.get("reaction_drift") is not None else r["reaction_total"]

    # Same liquidity bar the live trading pipeline actually gates entries on
    # (20-day avg $ volume, not market cap — a thin float can hide behind a
    # decent-looking company size), pre-computed off the earnings loop
    # (earnings.arm_liquidity) and already present on each row from
    # earnings_window() above. A live batch fetch for the whole window here
    # instead took over two minutes and made the page itself unusable — this
    # keeps that cost entirely off the request path.
    for e in upcoming + reported:
        v = e.get("low_liquidity")
        e["low_liquidity"] = bool(v) if v is not None else None

    return {"upcoming": upcoming, "reported": reported}


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path:
        candidate = (_STATIC / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(_STATIC.resolve()):
            return FileResponse(candidate)
    index = _STATIC / "index.html"
    if not index.is_file():
        return Response(
            "UI bundle missing — run `pnpm build` in alphadesk/ui", status_code=503
        )
    return FileResponse(index)
