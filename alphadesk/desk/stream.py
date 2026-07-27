"""On-demand 'Find Trades' — the v2 flow. Scans a broad news window, triages
opportunities, and debates each team-style, STREAMING every step so the
dashboard can show the agents thinking in real time.

Emits SSE-style event dicts via an async generator:
    status        — human-readable progress line
    triage_pick   — a symbol scout chose, with reason + edge hint
    skips         — the symbols scout passed on (with reasons)
    debate_start  — beginning deliberation on one symbol
    brief         — a specialist subagent's output
    thesis        — the researcher's opening call
    concern       — one critic attack (streamed individually)
    fact_flag     — a code-side fact-check flag
    rebuttal      — the researcher's defense/concession
    decision      — the judge's verdict + the final booked pick
    done          — the ranked board of all opportunities found

Reuses the exact team the autonomous engine used; only the orchestration
(sequential + streamed, broad news window) is new. No graph, no daemon.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from alphadesk.config import (
    EARNINGS_DRIFT_DAYS,
    EXPOSURE_MAX_SHOCKS,
    LEAN_EARNINGS_SKIP_NEWS,
    LEAN_MAX_DEBATES,
    LEAN_MODE,
    LEAN_NEWS_HOURS,
    LEAN_REVIEW_TRIGGER_ONLY,
    LEAN_SCOUT_MAX_CANDIDATES,
    MAX_CONCURRENT_WORKFLOWS,
    MODEL_MAP,
    REPICK_COOLDOWN_HOURS,
    SCOUT_MAX_CANDIDATES,
    SOLO_ARM_EVERY_N,
    WORLD_MAX_CATEGORIES,
    entry_fill_time,
    now_et,
    pinned_horizon,
    session,
)
from alphadesk.desk import (
    connections,
    debate,
    earnings_reader,
    gate,
    loner,
    news_check,
    notes,
    plan,
    review,
    scout,
    team,
)
from alphadesk.ingest import earnings, news, prices, world
from alphadesk.ledger import store
from alphadesk.llm import LLMError

log = logging.getLogger("alphadesk.stream")


def _ev(_type: str, **data):
    return {"type": _type, **data}


def _source_of(sym: str, earnings_syms: set, world_syms: set, ripple_syms: set) -> str:
    """Which ingestion channel surfaced this pick (most-specific wins), for
    cost/value attribution in the source scorecard."""
    su = sym.upper()
    if su in earnings_syms:
        return "EARNINGS"
    if su in ripple_syms:
        return "SPILLOVER"
    if su in world_syms:
        return "WORLD"
    return "FINANCIAL"


def _headlines(articles: list[dict]) -> list[str]:
    return scout.headline_rows(articles)


def _avg_sentiment(articles: list[dict]) -> float:
    return scout.avg_sentiment(articles)


def _intensity(articles: list[dict]) -> float:
    """Shock materiality proxy: |avg sentiment| × coverage."""
    return abs(_avg_sentiment(articles)) * len(articles)


def _materiality(articles: list[dict]) -> float:
    """Scout-window RANK signal: the biggest earnings REACTION across the articles (post-
    earnings drift is the favored edge and the reaction is its cleanest predictor), else the
    news intensity. Ranking the window by this — instead of market cap — is what surfaces the
    biggest movers to the scout rather than truncating them behind mega-caps (the THRM miss)."""
    reactions = [abs(a["reaction_pct"]) for a in articles if a.get("reaction_pct") is not None]
    return max(reactions) if reactions else _intensity(articles)


def _review_trigger(pos: dict, fresh: list) -> str | None:
    """Lean mode: is there anything NEW to judge about this open position? The ONLY
    trigger is fresh news in this run's pool — price-based exits belong to the
    watcher's pure-code level checks, never to an LLM (the reviewer is shown no
    prices). No news → auto-HOLD without spending a judgment call; the 180s watcher
    still closes on level crosses between runs, so the position is never unguarded."""
    return "fresh news" if fresh else None


_pending_run_picks: list[int] = []   # picks committed by the in-flight run, not yet finalised
_run_lock = asyncio.Lock()           # serialise runs: only one Find Trades at a time


async def stream_find_trades(hours: float = 48.0, max_debates: int = 6,
                             expose: bool = False, is_disconnected=None):
    """Public entry: serialise Find Trades runs so two can never overlap. Concurrent
    runs (two browser tabs, a reconnect) previously shared the module-global
    _pending_run_picks and one run could hard-delete the other's already-committed,
    already-streamed picks. A second run while one is in flight is now rejected rather
    than corrupting the first."""
    if _run_lock.locked():
        yield _ev("status", msg="A Find Trades run is already in progress — wait for it to finish.")
        yield _ev("done", board=[])
        return
    async with _run_lock:
        async for ev in _stream_find_trades_inner(hours, max_debates, expose, is_disconnected):
            yield ev


async def _stream_find_trades_inner(hours: float = 48.0, max_debates: int = 6,
                                    expose: bool = False, is_disconnected=None):
    """Async generator of deliberation events. Broad news window (default 48h —
    a batch run can afford to look far wider than the old live-tick engine).
    Stops early if the client disconnects (no more wasted LLM spend). Runs under
    _run_lock (see stream_find_trades), so its access to the module-global
    _pending_run_picks rollback list is single-file and race-free."""
    loop = asyncio.get_running_loop()
    global _pending_run_picks

    if LEAN_MODE:   # cost rails: shorter news window, fewer debates per run
        hours = min(hours, LEAN_NEWS_HOURS)
        max_debates = min(max_debates, LEAN_MAX_DEBATES)

    async def _gone() -> bool:
        return bool(is_disconnected and await is_disconnected())

    # Each run is independent: if the previous run was interrupted (tab refreshed
    # mid-hunt), roll back its abandoned in-progress picks so this run starts clean
    # — no half-finished ideas lingering, no cooldown blocking a full re-run.
    if _pending_run_picks:
        rolled = await loop.run_in_executor(None, store.delete_picks, list(_pending_run_picks))
        if rolled:
            log.info("Previous Find Trades run interrupted — rolled back %d in-progress pick(s)", rolled)
    _pending_run_picks = []

    yield _ev("status", msg="Reading the earnings calendar, then recent financial news…")
    since = datetime.now(timezone.utc).timestamp() - hours * 3600
    from datetime import datetime as _dt
    since_dt = _dt.fromtimestamp(since, tz=timezone.utc)

    # Earnings drift FIRST — the cached calendar is the PRIMARY candidate source:
    # free, high-signal, and the desk's cleanest edge (post-earnings drift). Gather
    # it before the slower, failure-prone news scan so it always leads. ingest/
    # earnings shapes the calendar rows into synthetic [EARNINGS] articles that flow
    # through the same scout → team pipeline; earnings_syms gives them front-of-line
    # priority against the scout-window cap below.
    candidates: dict[str, list[dict]] = {}
    earnings_syms: set[str] = set()
    if await _gone():
        return
    drift = await loop.run_in_executor(None, earnings.drift_candidates, EARNINGS_DRIFT_DAYS)
    for esym, e_arts in drift.items():
        earnings_syms.add(esym.upper())
        candidates[esym] = list(e_arts)   # earnings article(s) lead the bucket
    if drift:
        await loop.run_in_executor(None, store.record_ingest, "EARNINGS",
                                   sum(len(a) for a in drift.values()), len(drift))
        yield _ev("status", msg=f"{len(drift)} name(s) reported in the last "
                                f"{EARNINGS_DRIFT_DAYS}d — post-earnings-drift candidates (primary signal).")

    # Financial news — merged in AFTER earnings so the calendar leads. Fail-soft: a
    # news error just means we run on earnings drift (and world, if enabled) alone.
    # LEAN MODE, earnings-primary: a heavy earnings slate (≥ LEAN_EARNINGS_SKIP_NEWS
    # reporters) is where the edge lives — skip the news poll entirely and save the
    # fetch + enrichment spend.
    skip_news = LEAN_MODE and len(drift) >= LEAN_EARNINGS_SKIP_NEWS
    if skip_news:
        n, news_cands = 0, {}
        yield _ev("status", msg=f"Lean mode: {len(drift)} material earnings reporters — "
                                "earnings-primary run, news poll skipped.")
    elif await _gone():
        return
    else:
        yield _ev("status", msg=f"Scanning the last {int(hours)}h of financial news…")
        try:
            n, news_cands = await loop.run_in_executor(None, news.poll, since_dt)
        except Exception as exc:
            n, news_cands = 0, {}
            log.warning("News scan failed (%s) — proceeding on the earnings calendar", exc)
            yield _ev("status", msg=f"News scan failed ({exc}) — running on earnings drift.")
    await loop.run_in_executor(None, store.record_ingest, "FINANCIAL", n, len(news_cands))
    for sym, arts in news_cands.items():
        bucket = candidates.setdefault(sym, [])
        bucket.extend(arts)        # earnings article (if any) stays first
        # Anti-double-dip: a name may surface in both feeds on the SAME story —
        # dedup by id so it's never counted twice.
        seen: set = set()
        deduped = []
        for a in bucket:
            if a.get("id") not in seen:
                seen.add(a.get("id"))
                deduped.append(a)
        bucket[:] = deduped

    # World-news desk — OFF by default (WORLD_MAX_CATEGORIES=0): GDELT 429-throttles
    # hard and its enrichment dominated run time, and the button flow ran fine on
    # financial news + earnings alone. When enabled (>0), it's a CANDIDATE SOURCE
    # parallel to the financial-news scan: GDELT surfaces geopolitical / supply /
    # policy shocks that Polygon's company-centric feed misses, mapped to exposed
    # tradable names as HYPOTHESES the team must verify, merged into the SAME pool.
    # Fail-open; tracked in world_syms for scout-window priority (like ripples).
    world_syms: set[str] = set()
    if WORLD_MAX_CATEGORIES > 0 and not await _gone():
        world_events = 0
        try:
            world_events, world_cands = await loop.run_in_executor(None, world.poll, WORLD_MAX_CATEGORIES)
        except Exception as exc:
            world_cands = {}
            log.warning("World-news poll failed: %s", exc)
        await loop.run_in_executor(None, store.record_ingest, "WORLD", world_events, len(world_cands))
        for wsym, w_arts in world_cands.items():
            world_syms.add(wsym.upper())
            bucket = candidates.setdefault(wsym, [])
            bucket.extend(w_arts)
            # dedup by id — a name may surface from both the financial and world feeds
            seen = set()
            deduped = []
            for a in bucket:
                if a.get("id") not in seen:
                    seen.add(a.get("id"))
                    deduped.append(a)
            bucket[:] = deduped
        if world_cands:
            yield _ev("status", msg=f"World-news desk surfaced {len(world_cands)} "
                                    "geopolitically-exposed name(s) (hypotheses to verify).")

    # Position review — BEFORE hunting new trades (and even in a quiet window),
    # re-check every still-open TAKE from earlier runs against current price +
    # fresh news, and issue HOLD/EXIT with a reason. You may have traded the
    # original call, so exits are surfaced first and stamped in the ledger.
    open_positions = await loop.run_in_executor(None, store.open_taken_picks)
    if open_positions:
        yield _ev("status", msg=f"Reviewing {len(open_positions)} open position(s) from earlier runs…")
        for pos in open_positions:
            if await _gone():
                return
            psym = pos["symbol"]
            fresh = candidates.get(psym, [])
            if LEAN_MODE and LEAN_REVIEW_TRIGGER_ONLY and not _review_trigger(pos, fresh):
                yield _ev("position_hold", id=pos["id"], symbol=psym,
                          direction=pos["direction"], horizon_days=pos["horizon_days"],
                          reason="lean hold — no fresh news (the watcher guards target/stop)")
                continue
            # the reviewer is price-blind: it judges the thesis vs the NEWS only
            verdict = await loop.run_in_executor(
                None, review.review_position, pos, fresh, f"reeval-{pos['id']}")
            if verdict["decision"] == "EXIT":
                # price is fetched ONLY to stamp the realized exit — never shown to the reviewer
                pctx = await loop.run_in_executor(None, prices.get_context, psym)
                exit_px = (pctx or {}).get("last_price")
                fill = entry_fill_time(pos["ts"], pos.get("session"))
                if fill and now_et() < fill:
                    # Model A: the thesis died BEFORE the position could fill (still
                    # pre-open) — it's a CANCEL, not a held-then-exited trade. No
                    # realized P&L; the call is still graded at its horizon.
                    reason = f"not taken: {verdict['reason']}"
                    await loop.run_in_executor(
                        None, lambda: store.record_exit(pos["id"], reason))
                    yield _ev("position_exit", id=pos["id"], symbol=psym, direction=pos["direction"],
                              horizon_days=pos["horizon_days"], entry=None,
                              now=exit_px, reason=reason, not_taken=True)
                else:
                    # freeze realized performance at the exit price (same math as the
                    # target/stop watcher — distinct from the horizon grade)
                    spy_ctx = await loop.run_in_executor(None, prices.get_context, "SPY")
                    spy_now = (spy_ctx or {}).get("last_price")
                    entry = pos.get("entry_price") or pos.get("plan_entry")
                    perf = plan.realized_exit(pos["direction"], entry, exit_px,
                                              pos.get("spy_price"), spy_now,
                                              bool(pos.get("low_liquidity")))
                    await loop.run_in_executor(
                        None, lambda: store.record_exit(pos["id"], verdict["reason"], **perf))
                    yield _ev("position_exit", id=pos["id"], symbol=psym, direction=pos["direction"],
                              horizon_days=pos["horizon_days"], entry=pos.get("entry_price"),
                              now=exit_px, reason=verdict["reason"])
            else:
                yield _ev("position_hold", id=pos["id"], symbol=psym, direction=pos["direction"],
                          horizon_days=pos["horizon_days"], reason=verdict["reason"])

    if not candidates:
        await loop.run_in_executor(None, store.add_run, "FIND_TRADES", [])
        yield _ev("status", msg="No fresh catalysts found in that window.")
        yield _ev("done", board=[])
        return

    yield _ev("status", msg=f"{n} articles → {len(candidates)} companies with catalysts.")

    ripple_syms: set[str] = set()   # names the Connections desk surfaced (prioritized into scout)

    # Connections desk — expand the most material shocks into ripple candidates
    # (the connected, tradable names that haven't moved). Gated to the top-N
    # most intense shocks for cost. Set expose=False for a light run.
    if expose and candidates and not await _gone():
        shocks = sorted(candidates.items(), key=lambda kv: -_intensity(kv[1]))
        # Dedupe shocks that are the SAME underlying event: Polygon tags one story
        # with several ticker variants (GOOG/GOOGM/GOOGN), which would otherwise
        # burn the top-N slots web-mapping the same company. Skip a shock whose
        # headlines overlap one already chosen.
        shock_inputs: list[tuple[str, str]] = []
        seen_events: list[set] = []
        for sym, arts in shocks:
            if _intensity(arts) <= 0.1:
                continue
            key = {a.get("id") or a.get("title", "") for a in arts[:3]}
            if any(key & prev for prev in seen_events):
                continue
            seen_events.append(key)
            shock_inputs.append((sym, " | ".join(a.get("title", "")[:120] for a in arts[:3])))
            if len(shock_inputs) >= EXPOSURE_MAX_SHOCKS:
                break
        if shock_inputs:
            yield _ev("status",
                      msg=f"Connections desk mapping supply-chain ripples from "
                          f"{len(shock_inputs)} material shocks (web-verified)…")
            for sym, _ in shock_inputs:
                yield _ev("exposure_shock", symbol=sym)
            exp_results = await connections.run_connections(shock_inputs, "exposure")
            added = 0
            for res in exp_results:
                for c in res["candidates"]:
                    csym = c["symbol"]
                    if csym in candidates:
                        continue  # already surfaced directly by the news
                    sentiment = 0.5 if c["direction"] == "LONG" else -0.5
                    candidates.setdefault(csym, []).append({
                        "id": f"ripple-{res['shock']}-{csym}",
                        "title": f"[SPILLOVER from {res['shock']}] {c['chain'][:110]}",
                        "summary": f"HYPOTHESIS ({c['strength']}): {c['chain']}",
                        "source": "ExposureDesk", "url": "",
                        "published_at": since_dt.isoformat(), "category": "SPILLOVER",
                        "tickers": [csym],
                        "mentions": [{"symbol": csym, "sentiment": sentiment,
                                      "label": c["direction"].lower(), "category": "SPILLOVER"}],
                        "relations": [],
                    })
                    ripple_syms.add(csym)
                    yield _ev("exposure_candidate", shock=res["shock"], symbol=csym,
                              direction=c["direction"], chain=c["chain"], strength=c["strength"])
                    added += 1
            await loop.run_in_executor(None, store.record_ingest, "SPILLOVER", added, added)
            yield _ev("status", msg=f"Connections desk surfaced {added} ripple candidates.")

    # Anti-double-dip across runs — but not blind to NEW catalysts:
    #  • names we already HOLD → skip (the position review re-evaluated them; new
    #    adverse news there triggers an EXIT, so they're covered).
    #  • names debated within the cooldown → skip UNLESS a materiality check says a
    #    genuinely NEW catalyst arrived since that debate (same story != new event).
    held = {p["symbol"].upper() for p in open_positions}
    cooling = await loop.run_in_executor(None, store.symbols_debated_since, REPICK_COOLDOWN_HOURS)
    dropped: list[str] = []
    for s in list(candidates):
        su = s.upper()
        if su in held:
            candidates.pop(s, None)
            dropped.append(s)
            continue
        if su in cooling:
            last = await loop.run_in_executor(None, store.last_debate, su)
            ts = (last or {}).get("ts") or ""
            new_arts = [a for a in candidates[s] if str(a.get("published_at", "")) > ts]
            if new_arts:
                v = await loop.run_in_executor(
                    None, news_check.fresh_catalyst, s, last, new_arts, f"mat-{su}")
                if v.get("fresh_catalyst"):
                    yield _ev("status", msg=f"{s}: new development since last look — re-examining "
                                            f"({(v.get('reason') or '')[:90]}).")
                    continue  # a genuinely new catalyst — keep it in the pool
            candidates.pop(s, None)
            dropped.append(s)
    if dropped:
        yield _ev("status", msg=f"Skipped {len(dropped)} name(s): already held, or same story as a "
                                f"debate in the last {REPICK_COOLDOWN_HOURS}h (no re-dip).")
    if not candidates:
        await loop.run_in_executor(None, store.add_run, "FIND_TRADES", [])
        yield _ev("status", msg="Nothing fresh to debate after de-duping held/recent names.")
        yield _ev("done", board=[])
        return

    yield _ev("status", msg="Triaging…")

    # Build the scout window (price context per symbol). Reported names go FIRST (post-
    # earnings drift is the most-favored signal), then ripple/world candidates, then the rest
    # — but WITHIN each group ranked by MATERIALITY (earnings reaction size / news intensity),
    # NOT market cap, so the biggest movers reach the scout instead of being truncated behind
    # mega-caps (the THRM +22.7% miss). The window cap is SCOUT_MAX_CANDIDATES (raise for more
    # coverage at more scout tokens + price fetches).
    prioritized = ripple_syms | world_syms   # web-grounded / geopolitical hypotheses
    ordered = (
        sorted((kv for kv in candidates.items() if kv[0] in earnings_syms),
               key=lambda kv: -_materiality(kv[1]))
        + sorted((kv for kv in candidates.items()
                  if kv[0] in prioritized and kv[0] not in earnings_syms),
                 key=lambda kv: -_intensity(kv[1]))
        + sorted((kv for kv in candidates.items()
                  if kv[0] not in prioritized and kv[0] not in earnings_syms),
                 key=lambda kv: -_intensity(kv[1]))
    )
    window: dict[str, dict] = {}
    scout_cap = LEAN_SCOUT_MAX_CANDIDATES if LEAN_MODE else SCOUT_MAX_CANDIDATES
    scoped = ordered[:scout_cap]
    # price contexts fetched in PARALLEL — 30 sequential yfinance calls were seconds of dead time
    ctxs = await asyncio.gather(*[
        loop.run_in_executor(None, prices.get_context, sym) for sym, _ in scoped])
    for (sym, arts), ctx in zip(scoped, ctxs):
        window[sym] = {
            "headlines": _headlines(arts),
            "avg_sentiment": _avg_sentiment(arts),
            "price": ctx,
        }
    movers = await loop.run_in_executor(None, prices.movers)

    try:
        result = await loop.run_in_executor(None, scout.run_scout, window, movers)
    except LLMError as exc:
        await loop.run_in_executor(None, store.add_run, "FIND_TRADES", [])
        yield _ev("status", msg=f"Scout failed: {exc}")
        yield _ev("done", board=[])
        return

    picks = (result.get("picks") or [])[:max_debates]
    skips = result.get("skips") or []
    await loop.run_in_executor(None, store.record_skips, skips)  # grade forward: did we skip a mover?
    yield _ev("skips", skips=skips)
    for p in picks:
        yield _ev("triage_pick", symbol=p["symbol"], edge=p.get("edge_hint"),
                  reason=p.get("reason", ""))

    if not picks:
        await loop.run_in_executor(None, store.add_run, "FIND_TRADES", [])
        yield _ev("status", msg="Scout found no opportunities worth full analysis right now.")
        yield _ev("done", board=[])
        return

    # Pre-debate catalyst gate — drop picks with no real external catalyst BEFORE
    # the expensive debate (cheap haiku, fail-open; EARNINGS picks auto-pass — the
    # report IS the confirmed catalyst). Shared helper, same as the batch path.
    picks, gate_drops = await gate.screen_picks(picks, candidates, loop)
    for d in gate_drops:
        yield _ev("gate", symbol=d["symbol"], reason=d["reason"])
    if not picks:
        await loop.run_in_executor(None, store.add_run, "FIND_TRADES", [])
        yield _ev("status", msg="All picks gated out — no verifiable catalyst this scan.")
        yield _ev("done", board=[])
        return

    yield _ev("status", msg=f"Team debating {len(picks)} opportunities…")

    # Grounded calibration prior — the desk's own graded scorecard, computed
    # once per run and handed to every researcher/solo call as facts (not lessons).
    calibration = team.calibration_block(
        await loop.run_in_executor(None, store.stats))

    # Debates run CONCURRENTLY (bounded by MAX_CONCURRENT_WORKFLOWS) — a sequential
    # loop was ~6 serialized LLM calls per pick (~4 min each on a slow endpoint).
    # Per-pick event order is preserved; across picks the stream interleaves (each
    # event carries its symbol). Board order = completion order; the Head ranks
    # everything below regardless.
    board: list[dict] = []
    ev_q: asyncio.Queue = asyncio.Queue()
    pick_sem = asyncio.Semaphore(MAX_CONCURRENT_WORKFLOWS)
    _DONE = object()
    completed = 0   # drives the solo-arm cadence

    async def _one_pick(pick: dict) -> None:
        nonlocal completed
        sym = pick["symbol"]
        try:
            async with pick_sem:
                pick["source"] = _source_of(sym, earnings_syms, world_syms, ripple_syms)
                decision_id = f"{sym}-{uuid.uuid4().hex[:8]}"
                price_ctx = window.get(sym, {}).get("price")
                arts = candidates.get(sym, [])
                await ev_q.put(_ev("debate_start", symbol=sym, edge=pick.get("edge_hint")))
                try:
                    # brief subagents fan out in PARALLEL (market + news notes,
                    # fundamentals + options data) — feeding the researcher
                    fundamentals, opts = await asyncio.gather(
                        loop.run_in_executor(None, prices.get_fundamentals, sym),
                        loop.run_in_executor(None, prices.get_options_context, sym),
                    )
                    briefs = list(await asyncio.gather(
                        loop.run_in_executor(None, notes.market_brief, sym, price_ctx, fundamentals, arts, decision_id, opts),
                        loop.run_in_executor(None, notes.news_brief, sym, arts, decision_id),
                    ))
                    # If this pick just reported, attach the earnings evidence block —
                    # pure code-fetched FACTS (track record, revenue trend, revisions).
                    erow = await loop.run_in_executor(None, store.earnings_row, sym, EARNINGS_DRIFT_DAYS)
                    if erow:
                        briefs.append(await loop.run_in_executor(
                            None, earnings_reader.earnings_block, erow, f"eread-{sym}"))
                    for b in briefs:
                        await ev_q.put(_ev("brief", symbol=sym, **b))

                    history = await loop.run_in_executor(None, store.symbol_history, sym)
                    # shared team core — thesis/concern/fact_flag/rebuttal events,
                    # ledger write, and the row back via "_result"
                    row = None
                    async for ev in debate.deliberate(sym, pick, briefs, price_ctx, history,
                                                      calibration, "FIND_TRADES", decision_id):
                        if ev["type"] == "_result":
                            row = ev["row"]
                        else:
                            await ev_q.put(ev)
                except LLMError as exc:
                    await ev_q.put(_ev("status", msg=f"{sym}: dropped ({exc})"))
                    return

                if row is None:   # core yielded no result (shouldn't happen) — book nothing
                    return
                sess = session()   # for the solo arm's entry-price stamp below
                board.append(row)
                _pending_run_picks.append(row["id"])   # roll-backable if the run is interrupted
                await ev_q.put(_ev("decision", **row))
                completed += 1

                # Solo control arm — every Nth pick, one strong agent works the SAME
                # briefs blind to the team (kill-criterion #2).
                if SOLO_ARM_EVERY_N and completed % SOLO_ARM_EVERY_N == 0:
                    try:
                        loner_horizon = pinned_horizon(pick.get("edge_hint"))   # same pinned horizon as the team → apples-to-apples
                        s = await loop.run_in_executor(
                            None, lambda: loner.loner_analysis(
                                sym, pick["reason"], briefs, history, decision_id + "-solo",
                                calibration, loner_horizon))
                        s_model = s.pop("_downgraded_model", MODEL_MAP["loner"])
                        spy_ctx = await loop.run_in_executor(None, prices.get_context, "SPY")
                        _pending_run_picks.append(store.record_pick({
                            "symbol": sym, "arm": "LONER", "edge": pick.get("edge_hint"),
                            "trigger_src": "FIND_TRADES", "session": sess,
                            "direction": s["direction"], "horizon_days": loner_horizon,
                            "score": s["score"], "confidence": s["confidence"],
                            "approved": int(bool(s["approved"])), "triage_reason": pick["reason"],
                            "thesis": s["thesis"], "briefs": briefs, "model_tags": {"loner": s_model},
                            "low_liquidity": int(bool(price_ctx and price_ctx.get("low_liquidity"))),
                            "entry_price": (price_ctx or {}).get("last_price") if sess == "OPEN" else None,
                            "spy_price": (spy_ctx or {}).get("last_price"),
                        }))
                        await ev_q.put(_ev("loner", symbol=sym, direction=s["direction"],
                                           horizon_days=loner_horizon, score=s["score"]))
                    except LLMError as exc:
                        log.warning("Solo arm dropped %s: %s", sym, exc)
        finally:
            await ev_q.put(_DONE)

    tasks = [asyncio.create_task(_one_pick(p)) for p in picks]
    remaining = len(tasks)
    while remaining:
        if await _gone():   # client closed the tab — stop burning quota
            for t in tasks:
                t.cancel()
            log.info("Find Trades client disconnected — debates cancelled")
            return
        item = await ev_q.get()
        if item is _DONE:
            remaining -= 1
        else:
            yield item

    # TAKE-ALL: every debated pick is a position. Sort best-first (approved,
    # then conviction) so the concentration cap keeps the strongest of a cluster.
    for row in board:
        row["take"] = True
        row["chief_reason"] = ""
    board.sort(key=lambda r: (not r["approved"], -abs(r["conviction"] - 50)))
    capped = await loop.run_in_executor(None, team.apply_concentration_cap, board)
    for row in board:
        if row.get("sector"):
            store.update_pick(row["id"], sector=row["sector"], cluster=row["cluster"])
    store.add_run("FIND_TRADES", board)
    store.mark_taken([r["id"] for r in board if r.get("take")])
    _pending_run_picks = []
    if capped:
        yield _ev("status", msg="Concentration cap — held back "
                  + ", ".join(f"{r['symbol']} ({r['cap_reason']})" for r in capped))
    yield _ev("done", board=board)
