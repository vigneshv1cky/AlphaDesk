"""The grader — turns picks into a scorecard. Pure code, zero judgment.

Semantics:
  • Closed-market decisions (entry_price NULL) enter at the OPEN of the first
    trading day after the decision — never at a stale prior close.
  • ret_1d = close of entry day +1 trading day; ret_horizon = close of entry
    day + horizon_days trading days. Direction-aware (SHORT inverts).
  • Benchmark: SPY over the identical window (short picks benchmark against
    short-SPY, keeping alpha symmetric).
  • alpha_net = directional return − benchmark − friction. Friction is
    2 × FRICTION_BPS_PER_SIDE (doubled again for LOW_LIQUIDITY picks).
"""

import logging
from datetime import datetime, timezone

from alphadesk.config import ET, FRICTION_BPS_PER_SIDE
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.grader")

_history_cache: dict[str, object] = {}
_HISTORY_CACHE_MAX = 500


def _daily_history(symbol: str):
    """Daily OHLC frame for the last ~60 days (cached per grading pass, size-bounded)."""
    if symbol in _history_cache:
        return _history_cache[symbol]
    import yfinance as yf
    # auto_adjust=False → RAW OHLC. The stored entry_price is a raw fill; grading it
    # against a dividend-adjusted series (the yfinance default) made every dividend
    # payer book a fabricated loss over the hold. Raw-vs-raw on BOTH legs (stock + SPY)
    # is a consistent price-return; splits are handled explicitly at grade time.
    df = yf.Ticker(symbol).history(period="60d", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        _history_cache[symbol] = None
        return None
    df = df.tz_convert(ET) if df.index.tz is not None else df.tz_localize(ET)
    if len(_history_cache) >= _HISTORY_CACHE_MAX:
        _history_cache.pop(next(iter(_history_cache)), None)
    _history_cache[symbol] = df
    return df


def _beta(df, spy, lookback: int = 60) -> float:
    """Beta of the stock vs SPY from trailing daily returns (cov/var, population
    moments), clamped to [0, 3]. Defaults to 1.0 on thin/degenerate data — so
    alpha_adj with beta=1 and no borrow equals the SPY-relative alpha_net."""
    try:
        if spy is None:
            return 1.0
        sr = df["Close"].astype(float).pct_change().dropna()
        mr = spy["Close"].astype(float).pct_change().dropna()
        idx = sr.index.intersection(mr.index)
        if len(idx) < 20:
            return 1.0
        sr, mr = sr.loc[idx].tail(lookback), mr.loc[idx].tail(lookback)
        mmean = float(mr.mean())
        var = float(((mr - mmean) ** 2).mean())
        if var <= 0:
            return 1.0
        cov = float(((sr - float(sr.mean())) * (mr - mmean)).mean())
        return round(max(0.0, min(cov / var, 3.0)), 3)
    except Exception:
        return 1.0


def _borrow_cost(low_liquidity: bool, horizon_days: int) -> float:
    """Estimated SHORT borrow cost over the holding period, as a % of notional: the
    annualized rate (tiered by liquidity as a hard-to-borrow proxy) prorated over
    horizon trading days. Applied only to shorts in alpha_adj."""
    from alphadesk.config import SHORT_BORROW_APR, SHORT_BORROW_APR_ILLIQUID
    apr = SHORT_BORROW_APR_ILLIQUID if low_liquidity else SHORT_BORROW_APR
    return round(apr * horizon_days / 252.0, 3)


def _maybe_void(row: dict, df_missing: bool = False) -> bool:
    """Terminal stamp for picks that can NEVER grade, so they stop retrying hourly
    (grader zombies — `due_for_grading` is ORDER BY id LIMIT 100, so enough zombies
    at the front would starve real grading). Two classes:
      • never-filled 'not taken' rows (gap-skipped stale setup, limit never reached)
        whose fill time has already passed — by design there is no trade to grade;
      • symbols with no usable price history (delisted) well past their window — a
        long retry budget first, so a transient yfinance outage never voids a live pick.
    A pending fill (fill day still in the future) or a live position is NEVER voided."""
    if df_missing:
        try:
            decided = datetime.fromisoformat(row["ts"])
            if decided.tzinfo is None:
                decided = decided.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return False
        age_days = (datetime.now(timezone.utc) - decided).days
        if age_days <= int(row["horizon_days"]) + 10:
            return False
    else:
        if not (row.get("exit_ts") and row.get("entry_price") is None
                and (row.get("exit_reason") or "").startswith("not taken")):
            return False
        from alphadesk.config import entry_fill_time
        fill = entry_fill_time(row["ts"], row.get("session"))
        if fill is None or fill > datetime.now(ET):
            return False   # fill day hasn't arrived — a real grade may still happen
    store.update_pick(row["id"], graded_at=datetime.now(timezone.utc).isoformat())
    log.info("Voided ungradeable pick #%d %s (%s)", row["id"], row["symbol"],
             "no price history" if df_missing else row.get("exit_reason"))
    return True


def _entry(row: dict, df):
    """(entry_day, entry_price) for a pick, or None if not determinable yet. Shared
    by the horizon grade and the MFE/MAE path so both anchor identically. Entry
    precedence: (1) the BROKER's actual fill when the PM routed the pick (stamped
    broker_fill_price/ts — the honest entry, incl. extended-hours limit fills);
    (2) a live price for OPEN-session decisions; (3) the Model-A fill clock (next
    regular open) for closed-market decisions."""
    import pandas as pd

    from alphadesk.config import entry_fill_time

    days = df.index.normalize().unique()

    # 1. Broker fill — the PM routed this pick and it filled (possibly in extended
    # hours). Entry day = the fill's trading day, price = the actual fill price.
    bf = row.get("broker_fill_price")
    if bf and row.get("broker_fill_ts"):
        try:
            fdt = pd.Timestamp(row["broker_fill_ts"]).tz_convert(ET)
        except (ValueError, TypeError):
            fdt = None
        if fdt is not None:
            cand = days[days <= fdt.normalize()]
            if len(cand):
                return cand[-1], float(bf)

    # 2. OPEN-session picks filled LIVE at the decision (entry_price stamped then).

    # OPEN-session picks filled LIVE at the decision (entry_price stamped then).
    if row["session"] == "OPEN" and row["entry_price"] is not None:
        decided = datetime.fromisoformat(row["ts"])
        if decided.tzinfo is None:
            decided = decided.replace(tzinfo=timezone.utc)
        decided_day = pd.Timestamp(decided.astimezone(ET)).normalize()
        cand = days[days <= decided_day]
        if len(cand) == 0:
            return None
        return cand[-1], float(row["entry_price"])
    # closed-market decision → fills at the next regular 9:30 open (Model A). Use the
    # backfilled entry_price (= that open, stamped by the watcher) if present, else the
    # bar's open — both are the same fill price, so grade and live P&L stay consistent.
    fill = entry_fill_time(row["ts"], row["session"])
    if fill is None:
        return None
    fill_day = pd.Timestamp(fill).normalize()
    future = days[days >= fill_day]
    if len(future) == 0:
        return None
    entry_day = future[0]
    if row["entry_price"] is not None:
        return entry_day, float(row["entry_price"])   # already stamped (watcher applied the fill)
    # not yet stamped → determine the fill from the fill-day OHLC (market fills at the
    # open; a limit fills at its level only if price reached it, else None = not taken).
    from alphadesk.config import LIMIT_FILL_BUFFER_PCT, LIMIT_FILL_MIN_CUSHION_FRAC
    from alphadesk.desk.plan import limit_fill
    bar = df.loc[df.index.normalize() == entry_day]
    px = limit_fill(row["direction"], row.get("order_type"), row.get("plan_entry"),
                    float(bar["Open"].iloc[0]), float(bar["High"].iloc[0]),
                    float(bar["Low"].iloc[0]), LIMIT_FILL_BUFFER_PCT,
                    stop=row.get("plan_stop"), min_cushion_frac=LIMIT_FILL_MIN_CUSHION_FRAC)
    if px is None:
        return None      # a limit that never triggered → not taken (not graded)
    return entry_day, px


def _window_end(row: dict, days, entry_day):
    """Trading day the hold window closes on: the exit day if exited early, else
    the horizon day if reached, else the latest bar so far (running for open picks).
    Clamped to horizon so an exit stamp never runs the window past it."""
    import pandas as pd

    after = days[days > entry_day]
    horizon = int(row["horizon_days"])
    horizon_day = after[horizon - 1] if len(after) >= horizon else None
    if row.get("exit_ts"):
        ex = datetime.fromisoformat(row["exit_ts"])
        if ex.tzinfo is None:
            ex = ex.replace(tzinfo=timezone.utc)
        ex_day = pd.Timestamp(ex.astimezone(ET)).normalize()
        cand = days[days <= ex_day]
        end = cand[-1] if len(cand) else entry_day
        return min(end, horizon_day) if horizon_day is not None else end
    return horizon_day if horizon_day is not None else days[-1]


def _entry_and_outcomes(row: dict, df, spy) -> dict | None:
    """Compute gradable fields for one pick, or None if not yet gradable."""
    days = df.index.normalize().unique()
    ent = _entry(row, df)
    if ent is None:
        return None
    entry_day, entry_price = ent

    after = days[days > entry_day]

    def _close_after(n_days: int) -> float | None:
        if len(after) < n_days:
            return None
        day = after[n_days - 1]
        return float(df.loc[df.index.normalize() == day, "Close"].iloc[0])

    sign = 1.0 if row["direction"] == "LONG" else -1.0
    horizon = int(row["horizon_days"])
    out: dict = {}

    # Split-adjust the fixed raw entry onto the post-split basis of the raw close
    # series, so a split inside the hold window isn't read as a ~50%/100% fake move.
    # Dividends are intentionally left unadjusted (consistent price-return on both legs).
    if entry_price and "Stock Splits" in df.columns and len(after) >= 1:
        end_day = after[min(horizon, len(after)) - 1]
        sp = df.loc[(df.index.normalize() > entry_day)
                    & (df.index.normalize() <= end_day), "Stock Splits"]
        if (sp != 0).any():
            factor = float(sp[sp != 0].prod())
            if factor and factor != 1.0:
                entry_price = entry_price / factor

    close_1d = _close_after(1)
    if close_1d is not None and entry_price:
        out["ret_1d"] = round(sign * (close_1d - entry_price) / entry_price * 100, 3)

    close_h = _close_after(horizon)
    if close_h is None or not entry_price:
        # horizon not reached yet — partial grade only if 1d is available
        return out or None

    ret_h = sign * (close_h - entry_price) / entry_price * 100
    out["ret_horizon"] = round(ret_h, 3)

    # SPY over the identical window
    if spy is not None:
        sdays = spy.index.normalize().unique()
        s_entry_c = sdays[sdays >= entry_day]
        if len(s_entry_c) > 0:
            s_entry_day = s_entry_c[0]
            s_after = sdays[sdays > s_entry_day]
            if len(s_after) >= horizon:
                broker_ts = row.get("broker_fill_ts")
                if row.get("broker_fill_price") and broker_ts:
                    # Broker-filled (possibly extended-hours): benchmark from the last
                    # SPY close at/before the actual fill moment — the closest tradable
                    # benchmark point (pre-market fill → yesterday's close; after-hours
                    # fill → today's close; regular-hours fill → today's close).
                    import pandas as pd
                    fdt = pd.Timestamp(broker_ts).tz_convert(ET)
                    bc = sdays[sdays <= fdt.normalize()]
                    s_entry = float(spy.loc[spy.index.normalize() == bc[-1], "Close"].iloc[0]) \
                        if len(bc) else float(spy.loc[spy.index.normalize() == s_entry_day, "Close"].iloc[0])
                else:
                    # Match SPY's entry bar to the stock's fill: OPEN-session picks fill
                    # intraday at a live price → benchmark from the entry-day CLOSE; closed-
                    # market picks fill at the next 9:30 OPEN → benchmark from that OPEN.
                    # (Keying on entry_price-is-None was the bug: the watcher stamps
                    # entry_price on closed picks too, flipping them to CLOSE and silently
                    # dropping SPY's day-0 open→close move from every closed-market grade.)
                    spy_leg = "Close" if row["session"] == "OPEN" else "Open"
                    s_entry = float(spy.loc[spy.index.normalize() == s_entry_day, spy_leg].iloc[0])
                s_exit = float(spy.loc[spy.index.normalize() == s_after[horizon - 1], "Close"].iloc[0])
                spy_ret = (s_exit - s_entry) / s_entry * 100
                out["spy_ret_horizon"] = round(spy_ret, 3)
                benchmark = spy_ret if row["direction"] == "LONG" else -spy_ret
                friction = 2 * FRICTION_BPS_PER_SIDE / 100.0  # bps → %
                if row.get("low_liquidity"):
                    friction *= 2
                out["alpha_net"] = round(ret_h - benchmark - friction, 3)
                # Honest-alpha prototype, computed ALONGSIDE alpha_net (not replacing it):
                # (1) beta-adjust the benchmark, so a high-beta name up with the tape
                #     doesn't book its beta exposure as alpha; (2) charge SHORT borrow,
                #     which SPY-relative alpha_net ignored. beta=1 + no borrow ⇒ ==alpha_net.
                beta = _beta(df, spy)
                out["beta"] = beta
                borrow = (_borrow_cost(bool(row.get("low_liquidity")), horizon)
                          if row["direction"] == "SHORT" else 0.0)
                out["alpha_adj"] = round(ret_h - beta * benchmark - friction - borrow, 3)

    # Only stamp graded_at when the full grade (alpha vs SPY) resolved. Stamping it
    # otherwise (e.g. SPY history missing this pass) permanently marks the pick graded
    # with no alpha — it would never be retried and is invisible to the scorecard.
    if "alpha_net" in out:
        out["graded_at"] = datetime.now(timezone.utc).isoformat()
        if row["entry_price"] is None:
            out["entry_price"] = round(entry_price, 4)
    return out


def grade_due() -> int:
    """Grade all picks whose horizons have elapsed. Returns rows updated.
    Also updates the MFE/MAE path (open + closed) and skip grades each pass."""
    import time as _time
    _history_cache.clear()
    _time.sleep(2)  # rate-limit breathing room between passes

    # Batch-preload ALL symbols needed this pass (single download → no 429s)
    import yfinance as yf
    due = store.due_for_grading()
    due_react = store.due_reactions()
    due_skip = store.due_skips()
    all_syms = {"SPY"}
    for r in due:
        all_syms.add(r["symbol"])
    for r in due_react:
        all_syms.add(r["symbol"])
    for s in due_skip:
        all_syms.add(s["symbol"])
    if all_syms and len(all_syms) > 0:
        try:
            import pandas as pd
            sym_list = list(all_syms)
            df_batch = yf.download(sym_list, period="60d", interval="1d",
                                   group_by="ticker", progress=False, threads=True,
                                   auto_adjust=False)
            if df_batch is not None and not (isinstance(df_batch, pd.DataFrame) and df_batch.empty):
                is_multi = isinstance(df_batch.columns, pd.MultiIndex)
                for sym in all_syms:
                    try:
                        if is_multi and sym in df_batch.columns.get_level_values(0):
                            df = df_batch[sym].copy()
                        elif len(sym_list) == 1 and sym == sym_list[0]:
                            df = df_batch.copy()
                        else:
                            continue
                        if isinstance(df, pd.DataFrame) and len(df) > 0:
                            df_tz = df.tz_convert(ET) if df.index.tz is not None else df.tz_localize(ET)
                            _history_cache[sym] = df_tz
                    except Exception:
                        continue
        except Exception as exc:
            log.warning("Batch history download failed: %s — falling back to per-symbol", exc)
    spy = _history_cache.get("SPY") or _daily_history("SPY")
    _time.sleep(1)  # cooldown after batch download
    graded = 0
    calibrated_picks: list[dict] = []
    if spy is None:
        log.warning("SPY history unavailable — skipping pick/reaction/skip grading "
                    "this pass (MFE/MAE path still updates)")
    else:
        for row in store.due_for_grading():
            try:
                df = _daily_history(row["symbol"])
                if df is None:
                    _maybe_void(row, df_missing=True)
                    continue
                out = _entry_and_outcomes(row, df, spy)
                if not out:
                    _maybe_void(row)
                    continue
                store.update_pick(row["id"], **out)
                if "graded_at" in out:
                    graded += 1
                    log.info(
                        "Graded #%d %s %s %dd: ret=%.2f%% alpha_net=%s",
                        row["id"], row["symbol"], row["direction"], row["horizon_days"],
                        out.get("ret_horizon", float("nan")), out.get("alpha_net"),
                    )
                    # Collect for quant calibration
                    debate = row.get("debate") or {}
                    qsig = debate.get("quant_signals", {})
                    if qsig:
                        calibrated_picks.append({
                            "direction": out.get("graded_direction", row.get("direction", "")),
                            "pnl_pct": out.get("alpha_net", 0),
                            "signals": qsig,
                        })
            except Exception as exc:
                log.warning("Grading failed for #%d %s: %s", row["id"], row["symbol"], exc)
        grade_reactions()
        graded += grade_skips()
    grade_paths()
    # ── Quant calibration: update weights from graded outcomes ──
    if calibrated_picks:
        try:
            from alphadesk.config import QUANT_CALIBRATE
            if QUANT_CALIBRATE:
                from alphadesk.quant import calibrate as qcal
                weights = qcal.load_weights()
                for cp in calibrated_picks:
                    weights = qcal.online_update(
                        weights, cp["signals"], cp["direction"], cp["pnl_pct"])
                qcal.save_weights(weights)
                if len(calibrated_picks) >= 10:
                    qcal.batch_calibrate(calibrated_picks)
                # Also get graded picks with exit info for exit param optimization
                exited = store.get_graded_exits(days=30)
                if len(exited) >= 20:
                    qcal.optimize_exits(exited)
        except Exception as exc:
            log.debug("Quant calibration skipped: %s", exc)
    return graded


def grade_reactions() -> int:
    """Grade the reaction-gate A/B shadow cohort: forward alpha vs SPY in the reaction
    direction over the fixed horizon, for EVERY logged reporter (gate-passed and gate-
    dropped). Reuses the exact same entry clock + benchmark as booked picks (Model-A
    open fill, session-matched SPY bar, friction) so the two arms are apples-to-apples.
    The bucketed comparison (`abtest`) then shows whether forward alpha actually turns
    on at MATERIAL_REACTION_PCT or the gate is discarding quiet under-reactions."""
    spy = _daily_history("SPY")
    if spy is None:
        return 0   # no benchmark — wait for the next pass (never grade benchmark-less)
    graded = 0
    for r in store.due_reactions():
        try:
            df = _daily_history(r["symbol"])
            if df is None:
                continue
            # Shape a pick-like row so _entry_and_outcomes can grade it unchanged. The
            # MARKET session at sighting (mkt_session) drives the Model-A entry clock —
            # NOT the earnings-announcement session (BMO/AMC/DAY), which made every
            # shadow row fill at the NEXT open and skip day-1 of the drift (the biggest
            # day). Legacy rows (mkt_session NULL) keep the old next-open behaviour.
            row = {"session": r.get("mkt_session") or r["session"], "ts": r["ts"],
                   "entry_price": r.get("entry_price"),
                   "direction": r["direction"], "horizon_days": r["horizon_days"],
                   "low_liquidity": r["low_liquidity"], "exit_ts": None,
                   "order_type": None, "plan_entry": None, "plan_stop": None}
            out = _entry_and_outcomes(row, df, spy)
            if not out or "alpha_net" not in out:
                continue   # horizon/benchmark not resolvable yet — retry next pass
            store.update_reaction(
                r["id"], entry_price=out.get("entry_price"),
                ret_horizon=out.get("ret_horizon"),
                spy_ret_horizon=out.get("spy_ret_horizon"),
                alpha_net=out["alpha_net"], graded_at=out["graded_at"])
            graded += 1
        except Exception as exc:
            log.warning("Reaction grading failed for %s: %s", r["symbol"], exc)
    if graded:
        log.info("Graded %d reaction-gate A/B rows", graded)
    return graded


def grade_paths() -> int:
    """MFE/MAE over each position's hold window from daily High/Low — how far it
    ran in profit (max favorable) and how far underwater (max adverse) BEFORE it
    closed. Direction-aware, % vs entry. Running for open picks (updates each pass),
    frozen once exited or past horizon. Reuses the warm history cache; pure code."""
    due = store.picks_for_path()
    if not due:
        return 0
    updated = 0
    for row in due:
        try:
            df = _daily_history(row["symbol"])
            if df is None:
                continue
            ent = _entry(row, df)
            if ent is None:
                continue
            entry_day, entry_price = ent
            if not entry_price:
                continue
            days = df.index.normalize().unique()
            end_day = _window_end(row, days, entry_day)
            norm = df.index.normalize()
            window = df[(norm >= entry_day) & (norm <= end_day)]
            if window.empty:
                continue
            hi = float(window["High"].astype(float).max())
            lo = float(window["Low"].astype(float).min())
            if row["direction"] == "LONG":     # favorable = up, adverse = down
                mfe_hi = max(hi, float(row.get("exit_price") or 0))
                mae_lo = min(lo, float(row.get("exit_price") or float("inf")))
                mfe, mae = (mfe_hi - entry_price), (mae_lo - entry_price)
            else:                              # SHORT: favorable = down, adverse = up
                mfe_lo = min(lo, float(row.get("exit_price") or float("inf")))
                mae_hi = max(hi, float(row.get("exit_price") or 0))
                mfe, mae = (entry_price - mfe_lo), (entry_price - mae_hi)
            store.update_pick(row["id"],
                              mfe_pct=round(mfe / entry_price * 100, 3),
                              mae_pct=round(mae / entry_price * 100, 3))
            updated += 1
        except Exception as exc:
            log.warning("Path grading failed for #%d %s: %s", row["id"], row["symbol"], exc)
    return updated


def grade_skips() -> int:
    """Grade scout skips whose window has elapsed: a directionless |move vs SPY|
    over SKIP_GRADE_DAYS. missed=1 if it crossed the threshold — a dislocation we
    never looked at. Reuses the warm _history_cache from grade_due()."""
    import pandas as pd

    from alphadesk.config import (
        LOW_LIQUIDITY_DOLLAR_VOL,
        SKIP_GRADE_DAYS,
        SKIP_MISS_ABS_ALPHA,
    )
    due = store.due_skips()
    if not due:
        return 0
    spy = _daily_history("SPY")
    sdays = spy.index.normalize().unique() if spy is not None else None
    now_iso = datetime.now(timezone.utc).isoformat()
    graded = 0

    def _window_ret(df, sdates, entry_day) -> float | None:
        after = sdates[sdates > entry_day]
        if len(after) < SKIP_GRADE_DAYS:
            return None
        c0 = float(df.loc[df.index.normalize() == entry_day, "Close"].iloc[0])
        c1 = float(df.loc[df.index.normalize() == after[SKIP_GRADE_DAYS - 1], "Close"].iloc[0])
        return (c1 - c0) / c0 * 100 if c0 else None

    for row in due:
        try:
            df = _daily_history(row["symbol"])
            if df is None:  # unpriceable (delisted/odd suffix) — close it so we stop retrying
                store.update_skip(row["id"], abs_alpha=None, missed=0, graded_at=now_iso)
                graded += 1
                continue
            decided = datetime.fromisoformat(row["ts"])
            if decided.tzinfo is None:
                decided = decided.replace(tzinfo=timezone.utc)
            decided_day = pd.Timestamp(decided.astimezone(ET)).normalize()
            days = df.index.normalize().unique()
            entry_c = days[days >= decided_day]
            if len(entry_c) == 0:
                continue
            sym_ret = _window_ret(df, days, entry_c[0])
            if sym_ret is None:
                continue  # window not elapsed yet
            spy_ret = 0.0
            if sdays is not None:
                s_entry_c = sdays[sdays >= entry_c[0]]
                if len(s_entry_c) > 0:
                    spy_ret = _window_ret(spy, sdays, s_entry_c[0]) or 0.0
            abs_alpha = abs(sym_ret - spy_ret)
            # A big move in an ILLIQUID name is a FALSE miss — an untradeable pump
            # (the HIHO case), not an opportunity forgone. Keep abs_alpha for the
            # record but don't flag it as missed, so pumps never inflate the scout's
            # skip-miss rate (the signal fed back for grounded self-calibration).
            adv = float((df["Close"].astype(float) * df["Volume"].astype(float)).tail(20).mean())
            tradeable = adv >= LOW_LIQUIDITY_DOLLAR_VOL
            store.update_skip(
                row["id"], abs_alpha=round(abs_alpha, 3),
                missed=int(abs_alpha >= SKIP_MISS_ABS_ALPHA and tradeable), graded_at=now_iso)
            graded += 1
        except Exception as exc:
            log.warning("Skip grading failed for #%d %s: %s", row["id"], row["symbol"], exc)
    return graded
