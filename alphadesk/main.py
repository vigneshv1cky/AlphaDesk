"""AlphaDesk entrypoint.

  python -m alphadesk.main run        # scheduler + dashboard (the live system)
  python -m alphadesk.main backfill --hours 168
  python -m alphadesk.main grade      # one grading pass
  python -m alphadesk.main status     # ledger summary
"""

import argparse
import asyncio
import logging
import sys


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "claude_agent_sdk"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # yfinance logs BRK.A/.B-style "possibly delisted" at ERROR for tickers it
    # can't price; the app handles missing prices, so silence the spam.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def _web_server():
    import os

    import uvicorn

    from alphadesk.app.dashboard import app as dashboard_app

    return uvicorn.Server(uvicorn.Config(
        dashboard_app,
        host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),  # VM sets 0.0.0.0
        port=int(os.environ.get("DASHBOARD_PORT", "8000")),
        log_level="warning",
    ))


async def _serve() -> None:
    """v2 on-demand mode: dashboard + hourly portfolio grader (pure code).
    Trades run on button click / autorun; the grader keeps the paper
    portfolio marking even while nothing else runs."""

    async def _grader_loop():
        from alphadesk.app import scheduler
        from alphadesk.ledger.grader import grade_due
        loop = asyncio.get_running_loop()
        log = logging.getLogger("alphadesk.grader")
        while True:
            try:
                n = await loop.run_in_executor(None, grade_due)
                if n:
                    log.info("Graded %d positions", n)
            except Exception as exc:
                log.error("grader error: %s", exc)
            scheduler.beat()   # liveness for /healthz in dashboard mode (no ingest loop here)
            await asyncio.sleep(3600)

    async def _earnings_loop():
        from alphadesk.ingest import earnings
        loop = asyncio.get_running_loop()
        log = logging.getLogger("alphadesk.earnings")
        while True:
            try:
                await loop.run_in_executor(None, earnings.refresh_calendar)
                await loop.run_in_executor(None, earnings.arm_upcoming_reports)
            except Exception as exc:
                log.error("earnings refresh error: %s", exc)
            await asyncio.sleep(6 * 3600)   # 4×/day keeps upcoming + recent fresh

    async def _position_watch_loop():
        """Watch open picks between runs — the price-based exit (pure code):
        walk intraday minute bars, close at the first target/stop touched.
        OPEN hours: bar-based + Model-A fills; PRE/AFTER: spot-price for
        extended-hours fills; CLOSED: skip monitoring."""
        from alphadesk.config import (
            WATCH_INTERVAL_S,
            entry_allowed,
            entry_fill_time,
            now_et,
        )
        from alphadesk.config import session as market_session
        from alphadesk.desk.plan import (
            first_touch_exit,
            level_crossed,
            realized_exit,
        )
        from alphadesk.quant.watcher import session_close_due
        from alphadesk.ingest import prices
        from alphadesk.ledger import store
        loop = asyncio.get_running_loop()
        log = logging.getLogger("alphadesk.watch")
        last_check: dict[int, object] = {}   # pick_id → ET ts we last walked bars up to
        _fill_quality_flagged: set[int] = set()  # picks we've already warned about (one-shot)

        async def _sweep_session_close(open_pos, quotes) -> int:
            """NO-CARRY-OVER guarantee: force-close every filled open position at the
            current price once its session is at/after the close minute (PRE 9:25 /
            OPEN 15:55 / AFTER 19:55). The quant watcher enforces this per-pick with
            live stream prices; this sweep is the belt-and-suspenders so a position
            NEVER carries into another market even if tiered exits are disabled.
            record_exit is idempotent, so both closing a pick is safe."""
            due = session_close_due()
            if not due:
                return 0
            sess = due[0]
            spy_now = quotes.get("SPY")
            n = 0
            for p in open_pos:
                if not (p.get("taken")
                        and (p.get("entry_price") is not None
                             or p.get("broker_fill_price") is not None)
                        and p.get("plan_target") and p.get("plan_stop")):
                    continue
                cur = quotes.get(p["symbol"].upper())
                if not cur:
                    continue
                entry = (p.get("entry_price") or p.get("broker_fill_price")
                         or p.get("plan_entry"))
                perf = realized_exit(p["direction"], entry, cur,
                                     p.get("spy_price"), spy_now,
                                     bool(p.get("low_liquidity")))
                reason = f"session-close ({sess})"
                await loop.run_in_executor(
                    None, lambda pid=p["id"], r=reason, pf=perf:
                    store.record_exit(pid, r, **pf))
                n += 1
            if n:
                log.info("Session-close sweep: exited %d position(s) at close", n)
                from alphadesk.app.alerts import notify
                notify(f"Session close: exited {n} position(s)", "warn")
            return n
        while True:
            try:
                # Opt-in PAPER fills: sync broker order status → stamp real fills.
                from alphadesk.config import PAPER_TRADING
                if PAPER_TRADING:
                    try:
                        from alphadesk.desk import portfolio
                        await loop.run_in_executor(None, portfolio.reconcile_all)
                    except Exception as exc:
                        log.warning("portfolio reconcile error: %s", exc)
                sess = market_session()
                if sess == "CLOSED":
                    pass   # no monitoring outside market windows
                else:
                    # ── NO CARRY-OVER ACROSS MARKETS: every filled position exits
                    # at the close of the session it's in (PRE 9:25 / OPEN 15:55 /
                    # AFTER 19:55), so a trade NEVER survives into another session.
                    # The quant watcher closes per-pick with live stream prices;
                    # this sweep is the guarantee even if tiered exits are off. ──
                    if session_close_due():
                        open_pos = await loop.run_in_executor(None, store.live_picks)
                        if open_pos:
                            quotes = await loop.run_in_executor(
                                None, prices.latest_prices,
                                [p["symbol"] for p in open_pos] + ["SPY"])
                            await _sweep_session_close(open_pos, quotes)
                if sess == "OPEN":
                    # ── FULL-SESSION monitoring: bar-based exits + spot fallback. ──
                    open_pos = await loop.run_in_executor(None, store.live_picks)
                    quotes: dict[str, float] = {}
                    spy_now: float | None = None
                    # ── Fill-quality diagnostic: one-shot check for extended-hours
                    # fills that diverged significantly from the regular-session open.
                    # Thin extended-hours liquidity can produce fills at non-representative
                    # prices — this warns (doesn't change execution) so we can audit.
                    quality_check = [p for p in open_pos
                                     if p["id"] not in _fill_quality_flagged
                                     and p.get("broker_fill_price")
                                     and p.get("session") in ("PRE", "AFTER")
                                     and p.get("broker_fill_ts")]
                    if quality_check:
                        q_syms = sorted({p["symbol"] for p in quality_check})
                        q_quotes = await loop.run_in_executor(
                            None, prices.latest_prices, q_syms)
                        for p in quality_check:
                            _fill_quality_flagged.add(p["id"])
                            cur = q_quotes.get(p["symbol"].upper())
                            fill_px = float(p["broker_fill_price"])
                            if cur and fill_px:
                                div = abs(cur - fill_px) / fill_px * 100
                                if div > 2.0:
                                    log.warning(
                                        "Fill-quality: #%d %s ext-hours fill @ %.2f is "
                                        "%.1f%% from current price %.2f — possibly thin "
                                        "extended-hours fill",
                                        p["id"], p["symbol"], fill_px, div, cur)
                    # Session-scoped model: fills happen in the session they're
                    # booked for — OPEN/PRE/AFTER picks fill live at decision; night
                    # (CLOSED) picks fill at the next PRE open (4:00, in the PRE
                    # branch). Anything still unfilled once its fill moment has
                    # passed was never filled in its session → NOT TAKEN (no
                    # carry-over into another market).
                    now = now_et()
                    not_taken_ids: set[int] = set()
                    stale = [p for p in open_pos if p.get("entry_price") is None
                             and not p.get("broker_order_id")
                             and (ft := entry_fill_time(p["ts"], p.get("session"))) and ft <= now]
                    for p in stale:
                        reason = f"not taken: {p['symbol']} never filled in its session"
                        await loop.run_in_executor(
                            None, lambda i=p["id"], r=reason: store.record_exit(i, r))
                        not_taken_ids.add(p["id"])
                        log.info("Not taken #%d %s — no fill in its session",
                                 p["id"], p["symbol"])
                    # Only auto-exit positions that were actually TAKEN and FILLED —
                    # live_picks also carries counterfactuals the Head passed on and
                    # not-yet-filled limits (entry_price is NULL); exiting those would
                    # stamp realized P&L off plan_entry as a phantom fill. (The run-level
                    # review uses open_taken_picks, which already filters taken=1; the
                    # between-run watcher must match it.) A broker fill counts as filled.
                    monitorable = [p for p in open_pos if p["id"] not in not_taken_ids
                                   and p.get("taken")
                                   and (p.get("entry_price") is not None
                                        or p.get("broker_fill_price") is not None)
                                   and p.get("plan_target") and p.get("plan_stop")]
                    live_ids = {p["id"] for p in open_pos}
                    for stale in [i for i in last_check if i not in live_ids]:
                        last_check.pop(stale, None)
                    if monitorable:
                        quotes = await loop.run_in_executor(
                            None, prices.latest_prices,
                            [p["symbol"] for p in monitorable] + ["SPY"])
                        spy_now = quotes.get("SPY")
                        for p in monitorable:
                            cur = quotes.get(p["symbol"].upper())
                            entry = (p.get("entry_price") or p.get("broker_fill_price")
                                     or p.get("plan_entry"))
                            # Walk the true intraday PATH since we last looked (first sight:
                            # since the fill) to find the FIRST level touched — priced AT the
                            # level, gap-aware, and order-aware when a bar spans both. This
                            # replaces reading a single ~180s spot quote (which mis-booked the
                            # wrong level on a target-then-stop bar and froze P&L at whatever
                            # price the poll happened to catch). Falls back to the spot quote
                            # only when intraday bars are unavailable.
                            start = last_check.get(p["id"]) or entry_fill_time(
                                p["ts"], p.get("session"))
                            bars = (await loop.run_in_executor(
                                None, prices.intraday_bars, p["symbol"], start)
                                if start else [])
                            last_check[p["id"]] = now
                            ft = (first_touch_exit(p["direction"], p["plan_target"],
                                                   p["plan_stop"], bars) if bars else None)
                            if ft is None and not bars and cur:   # no bars → spot fallback
                                hit = level_crossed(p["direction"], cur,
                                                    p["plan_target"], p["plan_stop"])
                                if hit:
                                    ft = {"level": hit, "price": p["plan_target"]
                                          if hit == "target" else p["plan_stop"]}
                            if ft:
                                label = "target hit" if ft["level"] == "target" else "stopped out"
                                exit_px = ft["price"]
                                reason = f"{label} @ {exit_px} (first-touch {ft['level']})"
                                perf = realized_exit(p["direction"], entry, exit_px,
                                                     p.get("spy_price"), spy_now,
                                                     bool(p.get("low_liquidity")))
                                await loop.run_in_executor(
                                    None, lambda pid=p["id"], r=reason, pf=perf:
                                    store.record_exit(pid, r, **pf))
                                log.info("Auto-exit #%d %s %s — %s (%s%% vs SPY)",
                                         p["id"], p["symbol"], p["direction"], reason,
                                         perf.get("exit_alpha"))
                                continue
                            # No level crossed → nothing to do. The watcher is the ONLY
                            # price-based exit and it is pure code: no give-back screen,
                            # no LLM escalation on price action (a spent move closes at
                            # its target; a wrong one at its stop; anything in between
                            # keeps working to the levels or the horizon).


                elif sess in ("PRE", "AFTER"):
                    # ── EXTENDED-HOURS monitoring: spot-price only for filled
                    # positions (no intraday bars exist in extended hours). ──
                    open_pos = await loop.run_in_executor(None, store.live_picks)
                    if sess == "PRE":
                        # Night (CLOSED) picks enter once the session is live: stamp the
                        # live price — but only inside the session's ALLOWED entry window
                        # (past the START buffer, before the END entry buffer), so they
                        # skip the volatile open like any new entry. No extended trade →
                        # stays pending; the OPEN branch marks it not-taken if it never
                        # fills in the window.
                        now = now_et()
                        queued = [p for p in open_pos if p.get("entry_price") is None
                                  and not p.get("broker_order_id")
                                  and entry_allowed()
                                  and (ft := entry_fill_time(p["ts"], p.get("session"))) and ft <= now]
                        if queued:
                            q = await loop.run_in_executor(
                                None, prices.latest_prices,
                                [p["symbol"] for p in queued])
                            for p in queued:
                                px = q.get(p["symbol"].upper())
                                if px:
                                    await loop.run_in_executor(
                                        None, lambda i=p["id"], x=px: store.set_entry_price(i, x))
                                    log.info("Filled queued night pick #%d %s @ %.2f (PRE open)",
                                             p["id"], p["symbol"], px)
                    # Only monitor positions that already HAVE a fill (broker fill from
                    # extended-hours limit orders or already-stamped entry_price) — unfilled
                    # closed-market picks have nothing to monitor until the open.
                    ext_filled = [p for p in open_pos
                                  if p.get("taken")
                                  and (p.get("entry_price") is not None
                                       or p.get("broker_fill_price") is not None)
                                  and p.get("plan_target") and p.get("plan_stop")]
                    if ext_filled:
                        quotes = await loop.run_in_executor(
                            None, prices.latest_prices,
                            [p["symbol"] for p in ext_filled] + ["SPY"])
                        spy_now = quotes.get("SPY")
                        for p in ext_filled:
                            cur = quotes.get(p["symbol"].upper())
                            if cur is None:
                                continue
                            entry = (p.get("entry_price") or p.get("broker_fill_price")
                                     or p.get("plan_entry"))
                            hit = level_crossed(p["direction"], cur,
                                                p["plan_target"], p["plan_stop"])
                            if hit:
                                label = "target hit" if hit == "target" else "stopped out"
                                exit_px = p["plan_target"] if hit == "target" else p["plan_stop"]
                                reason = f"{label} @ {exit_px} (ext-hours spot {hit})"
                                perf = realized_exit(p["direction"], entry, exit_px,
                                                     p.get("spy_price"), spy_now,
                                                     bool(p.get("low_liquidity")))
                                await loop.run_in_executor(
                                    None, lambda pid=p["id"], r=reason, pf=perf:
                                    store.record_exit(pid, r, **pf))
                                log.info("Auto-exit #%d %s %s — %s (%s%% vs SPY) [ext-hours]",
                                         p["id"], p["symbol"], p["direction"], reason,
                                         perf.get("exit_alpha"))

            except Exception as exc:
                log.error("position watch error: %s", exc)
            from alphadesk.app import scheduler
            scheduler.beat()   # 180s liveness for /healthz (grader's hourly beat is too coarse)
            await asyncio.sleep(WATCH_INTERVAL_S)   # configurable; default 60s

    async def _autorun_loop():
        """Auto-fire Find Trades every AUTORUN_INTERVAL_MINUTES within [START, END] ET."""
        from datetime import datetime, timedelta

        from alphadesk.config import (
            AUTORUN_END_ET,
            AUTORUN_INTERVAL_MINUTES,
            AUTORUN_START_ET,
            ET,
            now_et,
        )
        from alphadesk.ledger import store
        log = logging.getLogger("alphadesk.autorun")
        if AUTORUN_INTERVAL_MINUTES <= 0 or not AUTORUN_START_ET:
            log.info("Auto-run disabled")
            return
        try:
            s_h, s_m = (int(x) for x in AUTORUN_START_ET.split(":"))
            e_h, e_m = (int(x) for x in AUTORUN_END_ET.split(":"))
        except Exception:
            log.error("Bad AUTORUN_START/END_ET — auto-run disabled")
            return
        log.info("Auto-run: every %dm, %s–%s ET", int(AUTORUN_INTERVAL_MINUTES),
                 AUTORUN_START_ET, AUTORUN_END_ET)
        running = False
        while True:
            try:
                now = now_et()
                in_window = (now.weekday() < 5
                             and (now.hour, now.minute) >= (s_h, s_m)
                             and (now.hour, now.minute) < (e_h, e_m))
                window_start = now.replace(hour=s_h, minute=s_m, second=0, microsecond=0)
                mins_since_start = (now - window_start).total_seconds() / 60
                interval_min = int(AUTORUN_INTERVAL_MINUTES)
                elapsed = int(mins_since_start) // interval_min
                current_slot = window_start + timedelta(minutes=elapsed * interval_min)
                if in_window and not running and now >= current_slot:
                    lt = store.last_run_time("FIND_TRADES")
                    last_slot = None
                    if lt:
                        try:
                            last_dt = datetime.fromisoformat(lt).astimezone(ET)
                            last_slot = last_dt.replace(minute=int(last_dt.minute // interval_min) * interval_min,
                                                        second=0, microsecond=0)
                        except (ValueError, TypeError):
                            pass
                    if last_slot is None or last_slot < current_slot:
                        running = True
                        try:
                            log.info("Auto-run: firing Find Trades")
                            from alphadesk.desk.stream import stream_find_trades
                            picks_found = 0
                            quant_picks = 0
                            quant_dropped = 0
                            async for _ev in stream_find_trades(hours=24.0):
                                t = _ev.get("type", "")
                                if t == "decision":
                                    picks_found += 1
                                elif t == "status":
                                    msg = _ev.get("msg", "")
                                    if "Quant pre-filter dropped" in msg:
                                        quant_dropped = int(
                                            msg.split()[4]) if len(msg.split()) > 4 else 0
                                    elif msg.startswith("Quant-only:"):
                                        quant_picks += 1
                            summary_parts = [f"{picks_found} pick(s)"]
                            if quant_picks:
                                summary_parts.append(f"{quant_picks} quant-only")
                            if quant_dropped:
                                summary_parts.append(f"{quant_dropped} pre-filtered out")
                            log.info("Auto-run complete — %s", ", ".join(summary_parts))
                            if picks_found:
                                from alphadesk.app.alerts import notify
                                notify("Find Trades: " + ", ".join(summary_parts), "pick")
                        finally:
                            running = False
            except Exception as exc:
                log.error("auto-run error: %s", exc)
            await asyncio.sleep(60)   # check each minute

    async def _quantity_watch_loop():
        """Quant-tiered exits (trailing stop, spike detection, stale expiry) — runs
        alongside the main position watcher. Uses live WebSocket prices when streaming
        is enabled, falls back to REST prices otherwise."""
        from alphadesk.config import QUANT_TIERED_EXITS, QUANT_STREAM_ENABLED, session as market_session
        log = logging.getLogger("alphadesk.quant")
        if not QUANT_TIERED_EXITS:
            return
        if QUANT_STREAM_ENABLED:
            try:
                from alphadesk.quant import stream as qstream
                log.info("Starting Alpaca WebSocket stream for real-time prices")
                await qstream.start_stream()
                qstream.register("SPY")
            except Exception as exc:
                log.warning("Alpaca stream start failed: %s — using REST price checks", exc)
        from alphadesk.quant import watcher as qwatcher
        from alphadesk.desk.plan import realized_exit
        from alphadesk.ledger import store as qstore
        loop = asyncio.get_running_loop()
        watch_interval = 5  # seconds — quant watcher runs faster than main watcher
        while True:
            try:
                sess = market_session()
                if sess == "CLOSED":
                    await asyncio.sleep(watch_interval)
                    continue
                positions = await loop.run_in_executor(None, qstore.live_picks)
                monitorable = [p for p in positions
                               if p.get("taken") and p.get("entry_price")
                               and p.get("plan_target") and p.get("plan_stop")]
                if not monitorable:
                    await asyncio.sleep(watch_interval)
                    continue
                live_prices = {}
                if QUANT_STREAM_ENABLED:
                    from alphadesk.quant import stream as qs
                    live_prices = qs.get_prices()
                    for p in monitorable:
                        s = p["symbol"].upper()
                        if s not in live_prices:
                            qs.register(s)
                    if "SPY" not in live_prices:
                        qs.register("SPY")
                if not live_prices:
                    from alphadesk.ingest import prices
                    syms = [p["symbol"] for p in monitorable] + ["SPY"]
                    live_prices = await loop.run_in_executor(None, prices.latest_prices, syms)
                # exit alpha needs SPY now — if the stream hasn't ticked SPY yet, pull it
                # via REST so quant exits still get graded against the benchmark
                if "SPY" not in live_prices:
                    from alphadesk.ingest import prices
                    spy_quotes = await loop.run_in_executor(None, prices.latest_prices, ["SPY"])
                    spy_px = spy_quotes.get("SPY")
                    if spy_px is not None:
                        live_prices["SPY"] = spy_px
                for p in monitorable:
                    cur = live_prices.get(p["symbol"].upper())
                    if not cur:
                        continue
                    entry = p["entry_price"]
                    if not entry:
                        continue
                    qwatcher.update_price(p["id"], cur)
                    result = qwatcher.check_exits(
                        p["id"], p["direction"], entry,
                        p["plan_target"], p["plan_stop"], cur)
                    if result:
                        spy_now = live_prices.get("SPY")
                        perf = realized_exit(p["direction"], entry, result["price"],
                                             p.get("spy_price"), spy_now,
                                             bool(p.get("low_liquidity")))
                        reason = f"quant-{result['reason']}"
                        await loop.run_in_executor(
                            None, lambda pid=p["id"], r=reason, pf=perf:
                            qstore.record_exit(pid, r, **pf))
                        qwatcher.clear_position(p["id"])
                        log.info("Quant exit #%d %s — %s (%.1f%% vs SPY)",
                                 p["id"], p["symbol"], reason,
                                 perf.get("exit_alpha"))
            except Exception as exc:
                log.error("quant watcher error: %s", exc)
            await asyncio.sleep(watch_interval)

    await asyncio.gather(_grader_loop(), _earnings_loop(), _autorun_loop(),
                         _position_watch_loop(), _quantity_watch_loop(),
                         _web_server().serve())


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="alphadesk")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dashboard", help="dashboard — trades run on button click")
    p_back = sub.add_parser("backfill")
    p_back.add_argument("--hours", type=float, default=72)
    p_desk = sub.add_parser("desk", help="convene the team NOW on recent news")
    p_desk.add_argument("--hours", type=float, default=8,
                        help="news lookback for the candidate window")
    sub.add_parser("grade")
    sub.add_parser("status")
    p_bt = sub.add_parser("backtest", help="replay past earnings → does the drift edge pay vs SPY?")
    p_bt.add_argument("--days", type=int, default=90, help="lookback window in calendar days")
    p_bt.add_argument("--horizon", type=int, default=1, help="forward trading days to grade")
    p_bt.add_argument("--selection", action="store_true",
                      help="grade in the quant composite's direction, bucketed by score (the selection test)")
    sub.add_parser("abtest", help="reaction-gate A/B: forward alpha bucketed by reaction size")
    sub.add_parser("alpha", help="honest alpha: SPY-relative alpha_net vs beta-adjusted, borrow-aware alpha_adj")
    sub.add_parser("earnings", help="refresh the earnings calendar and show upcoming / recent")
    args = parser.parse_args()

    if args.cmd == "dashboard":
        log = logging.getLogger("alphadesk")
        log.info("Dashboard on http://%s:%s — click Find Trades to run",
                 __import__("os").environ.get("DASHBOARD_HOST", "127.0.0.1"),
                 __import__("os").environ.get("DASHBOARD_PORT", "8000"))
        asyncio.run(_serve())
    elif args.cmd == "backfill":
        from alphadesk.ingest.earnings import refresh_calendar
        n = refresh_calendar(days_back=int(args.hours / 24) or 5)
        print(f"earnings calendar refreshed: {n} reporters")
    elif args.cmd == "desk":
        from alphadesk.desk.workflow import research_run
        from alphadesk.ingest.earnings import drift_candidates
        from alphadesk.config import EARNINGS_DRIFT_DAYS
        async def _adhoc() -> None:
            candidates = await asyncio.get_running_loop().run_in_executor(
                None, drift_candidates, EARNINGS_DRIFT_DAYS)
            print(f"{len(candidates)} earnings drift candidates")
            if candidates:
                ids = await research_run(candidates, trigger_src="DEEP_RUN")
                print(f"{len(ids)} picks booked")
            else:
                print("no candidates")
        asyncio.run(_adhoc())
    elif args.cmd == "grade":
        from alphadesk.ledger.grader import grade_due
        print(f"graded {grade_due()} picks")
    elif args.cmd == "status":
        from alphadesk.ledger import store
        print("ledger:", store.stats()["total"])
        print("tokens:", store.token_summary(days=1))
    elif args.cmd == "backtest":
        from alphadesk.ingest.earnings import refresh_calendar
        from alphadesk.ledger.backtest import backtest_drift, backtest_selection, report, report_selection
        print(f"Refreshing {args.days} days of earnings calendar…")
        n = refresh_calendar(days_back=args.days, days_fwd=0)
        print(f"{n} calendar rows")
        if args.selection:
            trades = backtest_selection(days=args.days, horizon=args.horizon)
            report_selection(trades)
        else:
            trades = backtest_drift(days=args.days, horizon=args.horizon)
            report(trades)
    elif args.cmd == "abtest":
        from alphadesk.config import MATERIAL_REACTION_PCT
        from alphadesk.ledger import store
        rows = store.reaction_ab_rows()
        # bucket by |reaction|; the gate keeps everything at/above MATERIAL_REACTION_PCT
        edges = [0.0, 1.0, MATERIAL_REACTION_PCT, 3.0, 6.0, float("inf")]
        labels = [f"<{edges[1]:g}%", f"{edges[1]:g}-{edges[2]:g}%",
                  f"{edges[2]:g}-3%", "3-6%", ">6%"]
        buckets: list[list[float]] = [[] for _ in labels]
        for r in rows:
            mag = abs(r["reaction_total"])
            for i in range(len(labels)):
                if edges[i] <= mag < edges[i + 1]:
                    buckets[i].append(r["alpha_net"])
                    break
        print(f"\n=== reaction-gate A/B — forward alpha vs SPY by reaction size "
              f"(gate keeps ≥ {MATERIAL_REACTION_PCT:g}%) ===")
        print(f"  {'bucket':10} {'gate':5} {'n':>4} {'mean α':>9} {'median α':>9} {'win%':>6}")
        for lab, vals in zip(labels, buckets):
            kept = "keep" if edges[labels.index(lab)] >= MATERIAL_REACTION_PCT else "drop"
            if vals:
                vals_sorted = sorted(vals)
                mean = sum(vals) / len(vals)
                median = vals_sorted[len(vals) // 2]
                win = 100.0 * sum(1 for v in vals if v > 0) / len(vals)
                print(f"  {lab:10} {kept:5} {len(vals):>4} {mean:>8.2f}% {median:>8.2f}% {win:>5.0f}%")
            else:
                print(f"  {lab:10} {kept:5} {0:>4} {'—':>9} {'—':>9} {'—':>6}")
        n = len(rows)
        if n < 20:
            print(f"\n  ({n} graded — too few to read yet; let it accumulate).")
        else:
            drop = [r["alpha_net"] for r in rows if not r["gate_passed"]]
            keep = [r["alpha_net"] for r in rows if r["gate_passed"]]
            dm = sum(drop) / len(drop) if drop else 0.0
            km = sum(keep) / len(keep) if keep else 0.0
            print(f"\n  dropped arm: n={len(drop)} mean α={dm:+.2f}%   "
                  f"kept arm: n={len(keep)} mean α={km:+.2f}%")
            print("  → dropped arm α ≥ kept arm α means the gate is cutting winners.")
    elif args.cmd == "alpha":
        from alphadesk.ledger import store
        rows = store.alpha_comparison()

        def _agg(rs):
            if not rs:
                return (0, None, None, None)
            net = sum(r["alpha_net"] for r in rs) / len(rs)
            adj = sum(r["alpha_adj"] for r in rs) / len(rs)
            beta = sum(r["beta"] for r in rs if r["beta"] is not None)
            nb = sum(1 for r in rs if r["beta"] is not None)
            return (len(rs), net, adj, (beta / nb) if nb else None)

        longs = [r for r in rows if r["direction"] == "LONG"]
        shorts = [r for r in rows if r["direction"] == "SHORT"]
        print("\n=== honest alpha — SPY-relative (alpha_net) vs beta-adjusted + borrow-aware (alpha_adj) ===")
        print(f"  {'cohort':7} {'n':>4} {'mean net':>10} {'mean adj':>10} {'β drag':>8} {'mean β':>7}")
        for name, rs in (("all", rows), ("longs", longs), ("shorts", shorts)):
            n, net, adj, beta = _agg(rs)
            if n:
                drag = net - adj
                bstr = f"{beta:.2f}" if beta is not None else "—"
                print(f"  {name:7} {n:>4} {net:>9.2f}% {adj:>9.2f}% {drag:>7.2f}% {bstr:>7}")
            else:
                print(f"  {name:7} {0:>4} {'—':>10} {'—':>10} {'—':>8} {'—':>7}")
        # by market session — which session's calls actually pay (PRE/OPEN/AFTER/CLOSED)
        sessions = sorted({r.get("session") or "?" for r in rows})
        if sessions:
            print(f"\n  by session (decision-time market session):")
            print(f"  {'session':8} {'n':>4} {'mean net':>10} {'mean adj':>10} {'win%':>6}")
            for s in sessions:
                rs = [r for r in rows if (r.get("session") or "?") == s]
                n, net, adj, _ = _agg(rs)
                win = 100.0 * sum(1 for r in rs if r["alpha_net"] > 0) / n if n else 0
                print(f"  {s:8} {n:>4} {net:>9.2f}% {adj:>9.2f}% {win:>5.0f}%")
        if not rows:
            print("\n  (no picks graded with both metrics yet — grade forward, then re-check).")
        else:
            print("\n  β drag = how much alpha_net OVERSTATED vs the beta-adjusted number "
                  "(positive = booked beta/borrow as alpha).")
    elif args.cmd == "earnings":
        from alphadesk.ingest import earnings
        from alphadesk.ledger import store
        print(f"calendar refreshed: {earnings.refresh_calendar()} rows")
        up = store.upcoming_earnings(days=7)
        print(f"\n=== reporting in the next 7 days ({len(up)}) ===")
        for e in up[:30]:
            print(f"  {e['report_date'][:16]}  {e['session'] or '?':3}  {e['symbol']:6}  est={e['eps_estimate']}")
        rec = store.recently_reported(days=3)
        print(f"\n=== reported in the last 3 days ({len(rec)}) ===")
        for e in rec:
            print(f"  {e['report_date'][:16]}  {e['symbol']:6}  est={e['eps_estimate']} act={e['eps_actual']} surprise={e['surprise_pct']}%")
    sys.exit(0)


if __name__ == "__main__":
    main()
