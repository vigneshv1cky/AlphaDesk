"""The terminal's HTTP surface — JSON API + the built SPA. No auth.

AlphaDesk is a consumption product: every endpoint here READS. Nothing books,
holds, closes or scores a position. The trading endpoints (/api/picks/*,
/api/live, /api/performance, /api/sessions, /api/timelines, /api/stats,
/api/sources, /api/quant/stats) were removed with the execution layer on
2026-08-18.
"""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
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
def api_chart(symbol: str, days: int = 2, range: str | None = None,
              interval: str | None = None):
    """OHLC + RSI-9 + MACD(12,26,9) series for the human decision chart.

    Always returns the data-quality block (coverage / median_gap_min /
    indicators_reliable). The UI must render that: on the free IEX feed an
    illiquid name's "1-minute" chart can be a handful of prints stretched
    across days, and it draws identically to a real one.
    """
    from alphadesk.providers import get_prices
    sym = "".join(c for c in symbol.upper() if c.isalnum() or c in ".-")[:12]
    if not sym:
        raise HTTPException(400, "bad symbol")
    # `range` (1D/5D/1M/3M/6M/YTD/1Y/5Y/MAX) picks the SERIES, not just its
    # length: 1D and 5D come off the minute feed, everything longer off daily
    # bars, because the minute feed reaches about 30 days. `days` stays for
    # callers that predate ranges.
    from alphadesk.ingest.prices import CHART_INTERVALS, CHART_RANGES
    if range and range.upper() not in CHART_RANGES:
        raise HTTPException(400, f"range must be one of {', '.join(CHART_RANGES)}")
    if interval and interval.lower() not in CHART_INTERVALS:
        raise HTTPException(400, f"interval must be one of {', '.join(CHART_INTERVALS)}")
    # A too-fine interval for the span is downgraded rather than refused, and
    # the response reports what was actually served — see resolve_interval.
    series = get_prices().chart_series(sym, days=days, range_key=range, interval=interval)
    if not series:
        raise HTTPException(404, f"no bars for {sym}")
    return series


# Two seconds between pushes per product. Long enough that a 420ms flash is
# followed by calm rather than the next flash, short enough that the strip is
# obviously live — and still thirty times the old sixty-second poll.
_CRYPTO_MIN_PUSH_S = float(os.environ.get("CRYPTO_MIN_PUSH_S", "2.0"))


@app.get("/api/stream-crypto")
async def api_stream_crypto(request: Request):
    """Live crypto prices for the ticker, pushed as Server-Sent Events.

    ALL the tape's crypto products on ONE connection, unlike the per-symbol
    equity stream above. The tape shows a handful of them and every reader
    wants the same set, so a stream per product would be several sockets
    carrying identical traffic.

    Coinbase rather than Alpaca, and it is not a close call: measured over the
    same window Coinbase pushed 517 BTC updates carrying 25 distinct prices in
    15 seconds where Alpaca's crypto feed gave 12 quotes in 20 and no trades.
    It also needs no credentials, so this works on a fresh clone.

    Path is /api/stream-crypto, not /api/stream/crypto, so it cannot ever be
    read as a symbol named "crypto" by the route above.
    """
    from alphadesk.ingest.cryptostream import crypto_products
    from alphadesk.ingest.cryptostream import stream as crypto

    products = crypto_products()

    async def events():
        import asyncio
        import json as _json

        taken = [p for p in products if crypto.acquire(p)]
        yield f"event: hello\ndata: {_json.dumps({'products': taken, 'live': bool(taken)})}\n\n"
        import time as _time
        last_price: dict[str, float] = {}
        last_sent: dict[str, float] = {}
        try:
            while True:
                if await request.is_disconnected():
                    break
                now = _time.monotonic()
                moved = []
                for p in taken:
                    tick = crypto.latest(p)
                    if not tick:
                        continue
                    px = tick.get("price")
                    # Deduped on PRICE, not on the exchange timestamp. Coinbase
                    # stamps every message, so timestamp-dedup re-sent a price
                    # that had not moved and spent the client's flash on it.
                    if px == last_price.get(p):
                        continue
                    # Rate limited per product. Bitcoin genuinely moves one to
                    # two times a second, and at that rate a 420ms flash is lit
                    # more than half the time — which reads as a permanent tint
                    # rather than a change. This strip is glanceable context by
                    # its own definition, not a quote feed, so it takes the
                    # latest price on an interval instead of every print.
                    if now - last_sent.get(p, 0.0) < _CRYPTO_MIN_PUSH_S:
                        continue
                    last_price[p] = px
                    last_sent[p] = now
                    moved.append(tick)
                if moved:
                    yield f"data: {_json.dumps({'ticks': moved})}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)
        finally:
            for p in taken:
                crypto.release(p)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/stream/{symbol}")
async def api_stream(symbol: str, request: Request):
    """Live trades for ONE symbol, pushed as Server-Sent Events.

    SSE rather than a websocket because this is one-way: the browser never has
    anything to say back, and EventSource brings its own reconnect, its own
    backoff and plain-HTTP transport for free. A websocket would add a second
    protocol to operate for no capability this needs.

    The upstream connection is shared and reference counted (ingest/stream.py)
    — Alpaca's free tier allows one per account, so this holds a reference for
    as long as the reader is here and drops it on disconnect.

    Async on purpose. Every other endpoint here is a sync def on the 40-worker
    threadpool, and a long-lived one of those would park a worker per viewer;
    this sits on the event loop instead, where an idle reader costs a sleeping
    task.
    """
    from alphadesk.ingest.stream import stream as market

    sym = "".join(c for c in symbol.upper() if c.isalnum() or c in ".-")[:12]
    if not sym:
        raise HTTPException(400, "bad symbol")

    async def events():
        import asyncio
        import json as _json

        subscribed = market.acquire(sym)
        # Say so immediately rather than leaving the client to infer it from
        # silence — on this feed silence is also what a quiet stock looks like.
        yield f"event: hello\ndata: {_json.dumps({'symbol': sym, 'live': subscribed})}\n\n"
        last_sent = None
        try:
            while True:
                if await request.is_disconnected():
                    break
                tick = market.latest(sym) if subscribed else None
                if tick and tick.get("at") != last_sent:
                    last_sent = tick.get("at")
                    yield f"data: {_json.dumps(tick)}\n\n"
                else:
                    # A comment line keeps proxies from timing the connection
                    # out while a symbol is genuinely quiet.
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)
        finally:
            if subscribed:
                market.release(sym)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        # nginx and friends buffer text/event-stream by default, which turns a
        # live feed into a batch delivered whenever the buffer fills.
        "X-Accel-Buffering": "no",
    })


@app.get("/api/quote/{symbol}")
def api_quote(symbol: str):
    """The equity-overview block for one symbol."""
    from alphadesk.providers import get_prices
    sym = "".join(c for c in symbol.upper() if c.isalnum() or c in ".-")[:12]
    if not sym:
        raise HTTPException(400, "bad symbol")
    q = get_prices().quote(sym)
    if not q:
        raise HTTPException(404, f"no quote for {sym}")
    return q


@app.get("/api/search")
def api_search(q: str = "", limit: int = 12):
    """Ticker/name search, so a symbol can be picked rather than typed exactly.

    Served from the cached Alpaca asset list — no vendor round trip, and it
    covers every symbol the terminal will actually accept, which a free-text
    box cannot promise.
    """
    from alphadesk.config import search_symbols, symbol_meta
    n = max(1, min(limit, 50))
    if q.strip():
        return {"results": search_symbols(q, limit=n), "trending": False}

    # Empty query: offer what is actually moving rather than a hardcoded list.
    # Most-active is the honest reading of "trending" from data this terminal
    # already has — it is the same ranking the movers tile shows.
    from alphadesk.providers import get_prices
    try:
        active = (get_prices().movers(top=n) or {}).get("most_active") or []
    except Exception:
        active = []
    out = []
    for row in active[:n]:
        meta = symbol_meta(row["symbol"]) or {"symbol": row["symbol"], "name": row.get("name"),
                                              "exchange": None, "asset_class": None}
        out.append(meta)
    return {"results": out, "trending": True}


@app.get("/api/fundamentals/{symbol}")
def api_fundamentals(symbol: str, period: str = "quarterly"):
    """Financial-statement series for plotting against price.

    Only metrics upstream actually reports for this company are returned, so
    the chart's Metrics menu offers nothing that would draw an empty line.
    """
    from alphadesk.ingest.prices import fundamentals_series
    sym = "".join(c for c in symbol.upper() if c.isalnum() or c in ".-")[:12]
    if not sym:
        raise HTTPException(400, "bad symbol")
    if period not in ("quarterly", "annual"):
        raise HTTPException(400, "period must be quarterly or annual")
    return fundamentals_series(sym, period)


@app.get("/api/movers")
def api_movers(top: int = 20):
    """Most active / gainers / losers, filtered for tradeability."""
    from alphadesk.providers import get_prices
    return get_prices().movers(top=max(1, min(top, 50)))


@app.get("/api/tape")
def api_tape():
    """The market strip pinned across the top of the terminal. Cached upstream
    for a minute — this is glanceable context, not a quote feed."""
    from alphadesk.providers import get_prices
    return {"tape": get_prices().market_tape()}


# Four, measured: nine concurrent per-symbol quotes made the upstream hand back
# 404s at random, and one at a time took 8.9s for eight symbols.
_QUOTES_CONCURRENCY = int(os.environ.get("QUOTES_CONCURRENCY", "4"))


@app.get("/api/quotes")
def api_quotes(symbols: str = ""):
    """Quotes for a BASKET, in one request.

    A theme page asking for nine symbols used to fire nine per-symbol requests
    at once. Every endpoint here is a sync def on a 40-worker threadpool, so
    that is nine threads hitting the upstream simultaneously — and it throttled,
    returning 404 for two or three of them at random. The rows that lost the
    race rendered as dashes, which reads as "this company has no price" rather
    than "we asked too fast".

    BOUNDED, not serial and not unbounded. Nine at once throttles; one at a
    time is nine times a single quote's latency, which measured 8.9s cold for
    eight symbols and is a page that looks broken while it loads. Four workers
    is the middle: fast enough to paint, slow enough that the upstream does not
    start refusing. The per-symbol cache underneath makes a warm basket
    effectively free (measured 0.002s), and this fills that same cache, so
    opening one of these names on Analysis afterwards is already answered.

    A symbol that genuinely has no quote comes back null rather than dropping
    out: the caller asked for a specific list and needs to know which member
    failed, not receive a shorter list.
    """
    from alphadesk.providers import get_prices
    wanted, seen = [], set()
    for raw in symbols.split(","):
        sym = "".join(c for c in raw.upper() if c.isalnum() or c in ".-")[:12]
        if sym and sym not in seen:
            seen.add(sym)
            wanted.append(sym)
    if not wanted:
        return {"quotes": {}}
    if len(wanted) > 50:
        raise HTTPException(400, "too many symbols (max 50)")
    prices = get_prices()

    def one(sym: str):
        try:
            return sym, prices.quote(sym)
        except Exception:
            return sym, None      # one bad symbol must not empty the basket

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=_QUOTES_CONCURRENCY) as pool:
        got = dict(pool.map(one, wanted))
    # Rebuilt in the ORDER ASKED, so the caller can render straight down its
    # own list without re-sorting a map whose iteration order it did not choose.
    return {"quotes": {sym: got.get(sym) for sym in wanted}}


def _clean_symbol(raw: str) -> str:
    return "".join(c for c in raw.upper() if c.isalnum() or c in ".-")[:12]


@app.get("/api/options/{symbol}")
def api_option_expirations(symbol: str):
    """Upcoming expiries for one underlying."""
    from alphadesk.providers import get_prices
    sym = _clean_symbol(symbol)
    if not sym:
        raise HTTPException(400, "bad symbol")
    return {"symbol": sym, "expirations": get_prices().option_expirations(sym)}


@app.get("/api/options/{symbol}/chain")
def api_option_chain(symbol: str, expiry: str = ""):
    """One expiry's chain. `expiry` is required — a chain without one is every
    strike of every expiry at once, which is thousands of rows and answers no
    question anyone asked."""
    from alphadesk.providers import get_prices
    sym = _clean_symbol(symbol)
    exp = "".join(c for c in expiry if c.isdigit() or c == "-")[:10]
    if not sym:
        raise HTTPException(400, "bad symbol")
    if len(exp) != 10:
        raise HTTPException(400, "expiry must be YYYY-MM-DD")
    return get_prices().option_chain(sym, exp)


@app.get("/api/themes")
def api_themes():
    """The curated baskets and their members. Pure config read — no prices, no
    ordering, no scoring. The page prices the members through /api/quotes,
    which batches them into one request."""
    from alphadesk.config import THEMES
    return {"themes": THEMES}


@app.get("/api/indices")
def api_indices():
    """The cross-asset board: indices, rates, commodities, FX. Wider than
    /api/tape on purpose — see INDEX_BOARD in config."""
    from alphadesk.providers import get_prices
    return {"indices": get_prices().index_board()}


@app.get("/api/crypto")
def api_crypto(top: int = 20):
    """{all, most_active, gainers, losers} for crypto, on a rolling 24h."""
    from alphadesk.providers import get_prices
    return get_prices().crypto_movers(top=max(1, min(top, 50)))


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
    import os

    from alphadesk.config import session as market_session
    from alphadesk.providers import available
    return {
        "uptime_s": round(_process_age_s()),
        "market": market_session(),
        "news": store.news_health(),
        # The live feed: whether the shared upstream socket is up and which
        # symbols currently have a reader. Worth surfacing for the same reason
        # the provider list is — "why is the price not moving" is usually
        # "nothing is subscribed", not "the market is quiet".
        "stream": _stream_status(),
        # Which plugins this deployment has, and which are selected. Worth
        # showing: with providers pluggable, "why is there no news" is usually
        # "the feed you configured isn't the one you think".
        "providers": {
            "available": available(),
            "selected": {
                "llm": os.environ.get("LLM_PROVIDER", "openai-compatible"),
                "news": os.environ.get("NEWS_PROVIDER", "polygon"),
                "prices": os.environ.get("PRICE_PROVIDER", "builtin"),
            },
        },
    }


def _stream_status() -> dict:
    """Never let a status read start a connection: importing the module is
    free, but `status()` on a stream nobody asked for would be a side effect
    of looking at the health page."""
    try:
        from alphadesk.ingest.stream import stream as market
        return market.status()
    except Exception:
        return {"connected": False, "available": False, "symbols": []}


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
    # Post-report drift is NOT reported here. It came from earnings_reactions,
    # a table the retired trading engine wrote to grade its own reaction gate —
    # nothing populates it any more, so serving it would mean serving stale
    # numbers from a dead system. The calendar answers "who reports when"; what
    # a name did afterwards belongs on its chart.

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


@app.get("/api/earnings/week")
def api_earnings_week(start: str | None = None):
    """One calendar week, day by day — the shape the Earnings page renders.

    `start` is any YYYY-MM-DD inside the wanted week; the week is normalised to
    the Sunday that contains it, so the caller can just pass "today" or step by
    seven days without doing calendar arithmetic itself. Omitted means this
    week.

    Every day is returned, including weekends and days with nothing on them —
    the strip across the top shows seven cells whatever the market did, and a
    missing day would silently shift the ones after it.
    """
    from datetime import date, timedelta

    from alphadesk.config import now_et

    try:
        anchor = date.fromisoformat(start) if start else now_et().date()
    except ValueError:
        raise HTTPException(400, "start must be YYYY-MM-DD") from None
    # Sunday-first, matching how the calendar is read: isoweekday() is Mon=1..Sun=7.
    sunday = anchor - timedelta(days=anchor.isoweekday() % 7)
    saturday = sunday + timedelta(days=6)

    rows = store.earnings_between(sunday.isoformat(), saturday.isoformat())
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["report_date"][:10], []).append(r)

    days = []
    for i in range(7):
        d = sunday + timedelta(days=i)
        key = d.isoformat()
        days.append({"date": key, "weekday": d.strftime("%a"),
                     "count": len(by_day.get(key, [])), "rows": by_day.get(key, [])})
    return {"start": sunday.isoformat(), "end": saturday.isoformat(),
            "today": now_et().date().isoformat(), "days": days}


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
