"""Dashboard — FastAPI serving the shadcn/ui SPA + JSON API. No auth."""

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

@app.get("/api/picks/{pick_id}")
def api_pick(pick_id: int):
    pick = store.get_pick(pick_id)
    if not pick:
        raise HTTPException(404, "no such pick")
    return pick


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


class ManualPick(BaseModel):
    symbol: str
    direction: str                      # LONG | SHORT
    thesis: str                         # required on purpose — see below
    target: float | None = None
    stop: float | None = None
    horizon_days: int = 1


@app.post("/api/picks/manual")
def api_manual_pick(body: ManualPick):
    """Book a trading decision. This is the ONLY way a trade enters the system
    — every autonomous booking path was deleted on 2026-08-16.

    trigger_src="HUMAN" tags the row. Nothing else needs wiring: quant/watcher.py
    discovers positions via open_taken_picks() regardless of who created them,
    so a manual entry gets the same target/stop/trailing management and the
    same forward grading against SPY automatically.

    `thesis` is mandatory. A decision with no recorded reason can't be
    learned from later, and the entire value of this system is that it
    refuses to let you misremember why you did something.

    Returns a `warning` (never a refusal) when the clock is against the trade:
    the session-close sweep is not optional, so booking inside the final
    ENTRY_BUFFER_MIN means the position gets dumped before it can work.
    Whether that's acceptable is the operator's call, not the server's.
    """
    from alphadesk.config import (MANUAL_MAX_QUOTE_AGE_S, MA_STOP_BACKSTOP_ATR,
                                  PLAN_TARGET_ATR, entry_allowed, session)
    from alphadesk.desk import plan
    from alphadesk.ingest import prices

    sym = "".join(c for c in body.symbol.upper() if c.isalnum() or c in ".-")[:12]
    direction = body.direction.upper().strip()
    if direction not in ("LONG", "SHORT"):
        raise HTTPException(400, "direction must be LONG or SHORT")
    if not sym:
        raise HTTPException(400, "bad symbol")
    if not body.thesis.strip():
        raise HTTPException(400, "thesis is required — record why you took this")

    pctx = prices.get_context(sym) or {}
    last = pctx.get("last_price")
    if not last:
        raise HTTPException(422, f"no live price for {sym} — not booking a blind entry")
    # The retired engine's rule was "a pick with no live trade is not taken",
    # but it tested last_trade_ts for EXISTENCE, which only means Alpaca ever
    # printed a trade — on a Sunday that's Friday's close. It got away with
    # that because it only ran while session()=="OPEN"; this endpoint can be
    # hit at any hour, so it checks FRESHNESS instead. Booking on a
    # stale price would record a fill that never happened and then grade it as
    # if it had. Also catches a halted symbol, whose last print goes stale
    # while the session is nominally open.
    from datetime import datetime, timezone
    rt_ts = pctx.get("last_trade_ts")
    age_s = (datetime.now(timezone.utc) - rt_ts).total_seconds() if rt_ts else None
    if age_s is None or age_s > MANUAL_MAX_QUOTE_AGE_S:
        mins = f"{age_s / 60:.0f} min" if age_s else "never"
        raise HTTPException(
            422,
            f"{sym}'s last trade was {mins} ago — that price isn't a fill you could "
            "get. Book while the symbol is actively trading.")

    target, stop = body.target, body.stop
    if target is None or stop is None:
        atr = pctx.get("atr_pct") or 2.0
        auto = plan.atr_plan(sym, direction, body.horizon_days, last, atr,
                             stop_atr_mult=MA_STOP_BACKSTOP_ATR)
        if auto:
            target = target if target is not None else auto["target"]
            stop = stop if stop is not None else auto["stop"]
        else:
            # atr_plan rejects the engine's wide-backstop geometry (reward/risk
            # 0.5 vs MIN_RISK_REWARD_RATIO 1.5), as the retired engine also hit.
            sign = 1 if direction == "LONG" else -1
            target = target if target is not None else round(last * (1 + sign * atr / 100 * PLAN_TARGET_ATR), 4)
            stop = stop if stop is not None else round(last * (1 - sign * atr / 100 * MA_STOP_BACKSTOP_ATR), 4)

    spy = (prices.get_context("SPY") or {}).get("last_price")
    pick_id = store.record_pick({
        "symbol": sym, "arm": "HUMAN", "edge": "MANUAL",
        "source": "HUMAN", "decision_id": f"h-{sym}",
        "trigger_src": "HUMAN", "session": session(),
        "direction": direction, "horizon_days": body.horizon_days,
        "score": 0.0, "adjusted_score": 0, "confidence": 0,
        "verdict": "HUMAN", "approved": 1,
        "triage_reason": body.thesis.strip(), "thesis": body.thesis.strip(),
        "debate": {}, "briefs": [], "model_tags": {"mode": "manual"},
        "low_liquidity": int(bool(pctx.get("low_liquidity"))),
        "skeptic_moved_score": 0.0, "arbiter_overrode": 0,
        "entry_price": last, "spy_price": spy,
        "plan_entry": round(last, 4), "plan_target": target, "plan_stop": stop,
        "plan_note": f"manual {direction} {sym}", "order_type": "market",
    })
    store.mark_taken([pick_id])
    return {"id": pick_id, "symbol": sym, "direction": direction,
            "entry": round(last, 4), "target": target, "stop": stop,
            "managed_by": "quant/watcher.py",
            "warning": None if entry_allowed() else
            "Booked inside the session's entry buffer — the close sweep will "
            "exit this before it has much room to work."}


@app.get("/api/stats")
def api_stats():
    s = store.stats()
    lt = store.last_run_time("FIND_TRADES")
    if lt and isinstance(s, dict):
        s.setdefault("total", {})["last_run"] = lt
    return s


@app.get("/api/tokens")
def api_tokens(days: int = 1):
    days = max(1, min(days, 365))   # a negative `days` becomes an invalid SQLite modifier → NULL → misleading data
    return {"days": days, "usage": store.token_summary(days)}


@app.get("/api/sources")
def api_sources(days: int = 30):
    days = max(1, min(days, 365))
    return {"days": days, "sources": store.source_scorecard(days)}


@app.get("/api/performance")
def api_performance(days: int = 30):
    """Performance analytics from realized exits: equity curve (equal-weight
    cumulative return), drawdown, per-trade + annualized-daily Sharpe, per-market
    P&L, and the full per-trade list for the drill-down. Pure arithmetic on the
    ledger — the honest read of whether the desk is making money."""
    import math
    from collections import defaultdict
    from datetime import datetime

    from alphadesk.config import ET

    days = max(1, min(days, 365))
    rows = store.performance_rows(days)

    curve = []
    cum = 0.0
    alpha_cum = 0.0
    for r in rows:
        cum += r.get("exit_return_pct") or 0
        # `or` treats a legitimate 0.0 exit_alpha (exactly matched SPY) as falsy
        # and wrongly falls through to alpha_net (a different value) — explicit
        # None checks instead, matching the frontend's own `t.exit_alpha ??
        # t.alpha_net` (PerformancePage.tsx), which was already correct.
        ea = r.get("exit_alpha")
        an = r.get("alpha_net")
        alpha_cum += ea if ea is not None else (an if an is not None else 0)
        curve.append({"ts": r["exit_ts"], "symbol": r["symbol"],
                      "cum": round(cum, 3), "alpha": round(alpha_cum, 3)})

    peak = float("-inf")
    max_dd = 0.0
    for p in curve:
        peak = max(peak, p["cum"])
        max_dd = max(max_dd, peak - p["cum"])

    rets = [float(r.get("exit_return_pct") or 0.0) for r in rows]
    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    var = sum((x - mean) ** 2 for x in rets) / n if n else 0.0
    std = var ** 0.5
    trade_sharpe = round(mean / std, 3) if n > 1 and std else None

    daily_map: dict[str, float] = defaultdict(float)
    for r in rows:
        try:
            day = datetime.fromisoformat(r["exit_ts"]).astimezone(ET).date().isoformat()
        except (ValueError, TypeError):
            continue
        daily_map[day] += r.get("exit_return_pct") or 0
    dvals = list(daily_map.values())
    daily_sharpe = None
    if len(dvals) >= 2:
        dm = sum(dvals) / len(dvals)
        dv = sum((x - dm) ** 2 for x in dvals) / len(dvals)
        dstd = dv ** 0.5
        if dstd:
            daily_sharpe = round(dm / dstd * math.sqrt(252), 3)

    per_market = {}
    for r in rows:
        s = r.get("session") or "?"
        pm = per_market.setdefault(s, {"n": 0, "pnl": 0.0, "wins": 0})
        pm["n"] += 1
        pm["pnl"] += r.get("exit_return_pct") or 0
        if (r.get("exit_return_pct") or 0) > 0:
            pm["wins"] += 1

    # Human vs machine, scored identically. The bot keeps booking on paper as
    # a control arm, so this is the comparison that says whether discretion is
    # adding anything — the question a P&L alone can never answer.
    by_decider: dict = {}
    for r in rows:
        src = r.get("trigger_src") or "?"
        who = "HUMAN" if src == "HUMAN" else "MACHINE"
        d = by_decider.setdefault(who, {"n": 0, "pnl": 0.0, "alpha": 0.0, "wins": 0})
        ret = r.get("exit_return_pct") or 0
        ea, an = r.get("exit_alpha"), r.get("alpha_net")
        d["n"] += 1
        d["pnl"] += ret
        d["alpha"] += ea if ea is not None else (an if an is not None else 0)
        if ret > 0:
            d["wins"] += 1
    for d in by_decider.values():
        d["pnl"] = round(d["pnl"], 3)
        d["alpha"] = round(d["alpha"], 3)
        d["mean_return"] = round(d["pnl"] / d["n"], 3) if d["n"] else None
        d["mean_alpha"] = round(d["alpha"] / d["n"], 3) if d["n"] else None
        d["win_rate"] = round(100.0 * d["wins"] / d["n"], 1) if d["n"] else None

    return {
        "days": days,
        "curve": curve,
        "n": n,
        "total_return": round(cum, 3),
        "mean_return": round(mean, 3),
        "win_rate": round(100.0 * sum(1 for x in rets if x > 0) / n, 1) if n else None,
        "max_drawdown": round(max_dd, 3),
        "trade_sharpe": trade_sharpe,
        "daily_sharpe": daily_sharpe,
        "per_market": {k: {**v, "pnl": round(v["pnl"], 3)} for k, v in per_market.items()},
        "by_decider": by_decider,
        "trades": rows,
    }


@app.get("/api/system")
def api_system():
    """System-health panel: is the desk alive and covering? Last run, run cadence
    today (how many actually booked), the coverage funnel, open positions, and
    process uptime."""
    from alphadesk.config import session as market_session
    s = store.stats()["total"]
    return {
        "last_run": s.get("last_run"),
        "runs_today": store.runs_summary_today(),
        "funnel_today": store.funnel_today(),
        "open_positions": store.open_position_count(),
        "graded": s.get("graded"),
        "exited": s.get("exited"),
        "uptime_s": round(_process_age_s()),
        "market": market_session(),
    }


@app.get("/api/quant/stats")
def api_quant_stats():
    """Signal-level performance: hit rate per signal, current weights,
    composite score distribution."""
    from alphadesk.quant import calibrate as qc
    weights = qc.load_weights()
    picks = store.live_picks() or []  # reuse live_picks for open positions
    graded = store.graded_signal_history() or []

    # Signal-level hit rates from graded picks
    signal_hits: dict[str, dict] = {}
    for g in graded:
        debate = g.get("debate") or {}
        qsig = debate.get("quant_signals", {})
        if not qsig:
            continue
        actual_dir = g.get("direction", "")
        alpha = float(g.get("alpha_net", 0))
        for name, val in qsig.items():
            if name not in signal_hits:
                signal_hits[name] = {"correct": 0, "total": 0, "total_alpha": 0.0}
            signal_hits[name]["total"] += 1
            if (val > 0 and actual_dir == "LONG") or (val < 0 and actual_dir == "SHORT"):
                signal_hits[name]["correct"] += 1
            signal_hits[name]["total_alpha"] += alpha

    signals = {}
    for name, data in signal_hits.items():
        n = data["total"]
        signals[name] = {
            "hit_rate": round(100 * data["correct"] / n, 1) if n else 0,
            "avg_alpha": round(data["total_alpha"] / n, 2) if n else 0,
            "n": n,
        }

    return {
        "weights": {k: round(v, 3) for k, v in weights.items()},
        "signals": signals,
        "open_positions": len(picks),
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
    eng = store.earnings_engagement([e["symbol"] for e in reported])
    reactions = store.earnings_reactions_batch([e["symbol"] for e in reported])
    for e in reported:
        m = eng.get(e["symbol"].upper())
        if m and (m.get("ts") or "")[:10] >= e["report_date"][:10]:
            e["engagement"] = m["state"]
            e["engagement_pick_id"] = m.get("pick_id")
            e["engagement_dir"] = m.get("direction")
            e["engagement_verdict"] = m.get("verdict")
            e["engagement_why"] = m.get("why")
        else:
            e["engagement"] = "UNSEEN"
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


def _alpha_so_far(direction: str, stock_then, cur, spy_then, spy_now):
    """Interim (unofficial) alpha: your return so far minus SPY over the SAME
    elapsed window, net of round-trip friction. None if a baseline is missing.
    This is a live mark, NOT the ledger grade (which settles only at the horizon).
    Same math the exit stamp freezes (plan.realized_exit) — one definition."""
    from alphadesk.desk.plan import realized_exit
    return realized_exit(direction, stock_then, cur, spy_then, spy_now)["exit_alpha"]


@app.get("/api/live")
def api_live():
    """Live tracking of open picks that carry a trade plan: current price vs
    entry/target/stop, P&L, alpha-so-far vs SPY, and a status. All pure arithmetic
    (code owns physics + scoring); the levels came from the desk. Alpha-so-far is a
    live mark, NOT the official grade — that still settles only at the horizon."""
    from alphadesk.config import entry_fill_time
    from alphadesk.config import session as market_session
    from alphadesk.desk import plan
    from alphadesk.ingest import prices
    picks = store.live_picks()
    quotes = prices.latest_prices([p["symbol"] for p in picks] + ["SPY"])
    spy_now = quotes.get("SPY")
    out = []
    for p in picks:
        cur = quotes.get(p["symbol"].upper())
        target, stop = p["plan_target"], p["plan_stop"]
        fill = entry_fill_time(p["ts"], p.get("session"))   # honest entry (9:30 open if decided off-hours)
        # A pick is only a POSITION once it has actually filled (entry_price stamped
        # at the open). Until then it's PENDING — no P&L, no vs-SPY (you're not in it),
        # regardless of market/limit. Filled → measure everything from the real entry.
        filled = p.get("entry_price") is not None
        # entry_ts: for immediate-fill picks (PRE/OPEN/AFTER), use decision time.
        # For CLOSED picks that filled at the next open, use the Model-A fill time.
        if filled:
            fill_ts = p["ts"] if p.get("session") != "CLOSED" else (fill.isoformat() if fill else p["ts"])
        else:
            fill_ts = fill.isoformat() if fill else p["ts"]
        row = dict(p, current=cur, pnl_pct=None, progress=None,
                   status=("pending" if not filled else "no quote"),
                   entry_ts=fill_ts,
                   alpha_so_far=None)
        if filled:
            entry = p["entry_price"]
            row["alpha_so_far"] = _alpha_so_far(p["direction"], entry, cur,
                                                p.get("spy_price"), spy_now)
            if cur and entry and target and stop and target != stop:
                up = p["direction"] == "LONG"
                row["pnl_pct"] = round((1.0 if up else -1.0) * (cur - entry) / entry * 100, 2)
                prog = (cur - stop) / (target - stop) if up else (stop - cur) / (stop - target)
                row["progress"] = round(max(0.0, min(1.0, prog)), 3)  # 0 = at stop, 1 = at target
                hit = plan.level_crossed(p["direction"], cur, target, stop)
                if hit == "target":
                    row["status"] = "target hit"
                elif hit == "stop":
                    row["status"] = "stopped out"
                elif abs(cur - target) <= 0.15 * abs(target - entry):
                    row["status"] = "near target"
                elif abs(cur - stop) <= 0.15 * abs(stop - entry):
                    row["status"] = "near stop"
                else:
                    row["status"] = "working"
        elif cur and target != stop:   # pending: still show where price sits on the track
            up = p["direction"] == "LONG"
            prog = (cur - stop) / (target - stop) if up else (stop - cur) / (stop - target)
            row["progress"] = round(max(0.0, min(1.0, prog)), 3)
        out.append(row)
    return {"live": out, "market": market_session()}


def _is_not_taken(exit_ts: str | None, exit_reason: str | None, fill) -> bool:
    """True if a call was never actually held — a CANCEL, not a held-then-exited
    trade. Two ways: a close stamped BEFORE the fill (pre-open thesis death), or a
    LIMIT order whose level the market never reached (marked 'not taken')."""
    if not exit_ts:
        return False
    if str(exit_reason or "").startswith("not taken"):
        return True
    if fill is None:
        return False
    from datetime import datetime
    try:
        return datetime.fromisoformat(exit_ts) < fill   # tz-aware (exit UTC, fill ET)
    except (ValueError, TypeError):
        return False


@app.get("/api/sessions")
def api_sessions(days: int = 14):
    """Picks grouped by the market window they were DECIDED in — day market
    (regular hours), extended market (pre + after-hours), night market
    (overnight/weekend) — with per-window aggregates. Only exited picks:
    target hit or stopped out."""
    GROUP = {"OPEN": "day", "PRE": "extended", "AFTER": "extended", "CLOSED": "night"}
    out: dict[str, list] = {"day": [], "extended": [], "night": []}
    for r in store.recent_team_picks(days=days):
        if r.get("exit_ts"):   # only exited picks: target hit or stopped out
            out.setdefault(GROUP.get(r.get("session") or "", "night"), []).append(r)
    agg = {}
    for g, rows in out.items():
        rows.sort(key=lambda r: r["ts"], reverse=True)
        graded = [x for x in rows if x.get("alpha_net") is not None]
        agg[g] = {
            "n": len(rows),
            "open": 0,
            "graded": len(graded),
            "wins": sum(1 for x in graded if x["alpha_net"] > 0),
            "avg_alpha": round(sum(x["alpha_net"] for x in graded) / len(graded), 2) if graded else None,
        }
    return {"sessions": out, "agg": agg}


@app.get("/api/timelines")
def api_timelines(days: int = 30):
    """Track record grouped BY STOCK: each symbol's ordered calls with outcomes
    (open → live P&L; graded → vs S&P; exited), the desk's current stance, and
    whether that stance changed over time (buy→sell / an exit)."""
    days = max(1, min(days, 365))
    from alphadesk.config import entry_fill_time
    from alphadesk.config import session as market_session
    from alphadesk.ingest import prices
    rows = store.recent_team_picks(days)
    # Track Record: only exited picks by default. Open positions belong in Live.
    rows = [r for r in rows if r.get("exit_ts")]
    by_sym: dict[str, list[dict]] = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    open_syms = [s for s, evs in by_sym.items()
                 if any(e["graded_at"] is None and e["exit_ts"] is None for e in evs)]
    quotes = prices.latest_prices(open_syms + ["SPY"])
    spy_now = quotes.get("SPY")

    symbols = []
    for sym, evs in by_sym.items():
        # Sort by most recent activity: exit_ts first, then ts, latest on top
        evs.sort(key=lambda e: e.get("exit_ts") or e.get("ts") or "", reverse=True)
        events = []
        for e in evs:
            # a close stamped BEFORE the position could fill (pre-open) is a CANCEL,
            # not a held-then-exited trade (Model A). Detect by timestamp (catches
            # historical rows too), not just the "not taken:" reason marker.
            fill = entry_fill_time(e["ts"], e.get("session"))   # honest entry (9:30 open if decided off-hours)
            not_taken = _is_not_taken(e["exit_ts"], e.get("exit_reason"), fill)
            state = ("not_taken" if not_taken
                     else "exited" if e["exit_ts"]
                     else "graded" if e["graded_at"] else "open")
            # broker_fill_price is the price actually paid — prefer it over
            # entry_price (a pre-fill decision-time quote that can differ
            # sharply from the real fill on a thin/low-liquidity name) so the
            # displayed entry and any P&L derived from it reflect the real trade.
            real_entry = e.get("broker_fill_price") or e.get("entry_price")
            ev = dict(e, state=state, entry_ts=(fill.isoformat() if fill else e["ts"]),
                      entry_price=real_entry,
                      current=None, pnl_pct=None, status=None, alpha_so_far=None)
            if state == "open":
                cur = quotes.get(sym.upper())
                ev["current"] = cur
                # only a POSITION once filled (entry_price stamped at the open) —
                # a pending pick has no P&L / vs-SPY yet, market or limit.
                if e.get("entry_price") is not None:
                    entry, target, stop = e["entry_price"], e["plan_target"], e["plan_stop"]
                    ev["alpha_so_far"] = _alpha_so_far(e["direction"], entry, cur,
                                                       e.get("spy_price"), spy_now)
                    if cur and entry and target and stop and target != stop:
                        up = e["direction"] == "LONG"
                        ev["pnl_pct"] = round((1.0 if up else -1.0) * (cur - entry) / entry * 100, 2)
                        hit_t = cur >= target if up else cur <= target
                        hit_s = cur <= stop if up else cur >= stop
                        ev["status"] = ("target hit" if hit_t else "stopped out" if hit_s
                                        else "working")
                else:
                    ev["status"] = "pending"
            events.append(ev)
        latest = evs[-1]
        latest_not_taken = _is_not_taken(
            latest["exit_ts"], latest.get("exit_reason"),
            entry_fill_time(latest["ts"], latest.get("session")))
        current = ("NOT_TAKEN" if latest_not_taken
                   else "EXITED" if latest["exit_ts"]
                   else latest["direction"] if latest["graded_at"] is None else "CLOSED")
        changed = len({e["direction"] for e in evs}) > 1 or any(e["exit_ts"] for e in evs)
        symbols.append({"symbol": sym, "current": current, "changed": changed,
                        "last_ts": latest.get("exit_ts") or latest["ts"], "events": events})
    symbols.sort(key=lambda s: s["last_ts"] or "", reverse=True)
    return {"symbols": symbols, "market": market_session()}


# There is no endpoint that starts a trading run. The batch Find Trades
# scanner went in 2026-08-13, its per-candidate replacement went with all the
# other bots on 2026-08-16. Trades enter this system exactly one way now:
# POST /api/picks/manual, from a human.


# ---------------------------------------------------------------------------
# SPA — static bundle with index fallback (client handles the rest)
# ---------------------------------------------------------------------------

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
