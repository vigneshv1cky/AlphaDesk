"""Backtest harness — does the drift edge actually pay on history?

Replays past earnings reports with the SAME entry/benchmark/friction model as a
live pick: for each report in the window it reconstructs the reaction the desk
would have seen at the first post-report open (vs the pre-report baseline), then
grades the forward move in the reaction direction vs SPY over `horizon` trading
days, net friction. This answers the question the tiny live ledger can't yet: is
post-earnings drift real, and where does it turn on (reaction size / BMO vs AMC)?

Pure code, historical daily bars only. v1 tests the RAW alpha thesis — it does not
apply the quant composite weights (that is the selection test, next).

Caveat: this models the OPEN-session trade (enter at the 9:30 open, exit at the
close). PRE/AFTER extended-hours entries aren't in daily bars; a name whose move
happened in pre-market is still captured via its open-vs-baseline reaction.
"""

import logging

log = logging.getLogger("alphadesk.backtest")

BUCKETS = [(0.0, 1.5), (1.5, 3.0), (3.0, 6.0), (6.0, 10.0), (10.0, float("inf"))]
BUCKET_LABELS = ["<1.5%", "1.5-3%", "3-6%", "6-10%", ">10%"]


def _bucket(mag: float) -> int:
    for i, (lo, hi) in enumerate(BUCKETS):
        if lo <= mag < hi:
            return i
    return len(BUCKETS) - 1


def _load_hist(symbols: list[str], period: str = "6mo", chunk: int = 40) -> dict:
    """Batch-download raw daily OHLC for all symbols + returns {SYM: df}.

    Chunked (~40 tickers per yfinance call, a short pause between chunks): a full
    window can be 2000+ reporters, and a single threads=True download of that many
    spawns a thread per ticker and dies with 'can't start new thread' on a small
    VM — and hammering yfinance wholesale trips its rate limiter. Callers should
    pass a BOUNDED symbol set (see backtest_drift's max_symbols cap)."""
    import time as _time

    import pandas as pd
    import yfinance as yf

    out = {}
    syms = sorted({s.upper() for s in symbols if s})
    if not syms:
        return out
    for i in range(0, len(syms), chunk):
        batch = syms[i:i + chunk]
        try:
            df = yf.download(batch, period=period, interval="1d", group_by="ticker",
                             auto_adjust=False, progress=False, threads=True)
            if df is not None:
                multi = isinstance(df.columns, pd.MultiIndex)
                for s in batch:
                    try:
                        if multi and s in df.columns.get_level_values(0):
                            sub = df[s]
                        elif len(batch) == 1:
                            sub = df
                        else:
                            continue
                        sub = sub.dropna(subset=["Close"]) if isinstance(sub, pd.DataFrame) else None
                        if sub is not None and len(sub) > 0:
                            out[s] = sub
                    except Exception:
                        continue
        except Exception as exc:
            log.warning("history batch %d failed: %s", i // chunk, exc)
        _time.sleep(0.4)   # be polite to yfinance between chunks
    return out


def _days(df) -> list:
    return list(df.index.normalize().unique())


def _bar(df, day, col) -> float | None:
    try:
        rows = df.loc[df.index.normalize() == day]
        return float(rows[col].iloc[0]) if len(rows) else None
    except Exception:
        return None


def _composite(df, dlist, entry_day, entry_open, baseline, reaction_pct, weights) -> dict | None:
    """Approximate the quant composite from historical daily bars, using only what
    was visible at the DECISION moment (the open). Live-only inputs — options
    implied move, short interest, sector, spread — are absent here and contribute 0.
    The composite is what the desk actually trades on, so this is the selection test."""
    from alphadesk.quant import signals as qs

    eidx = dlist.index(entry_day)
    closes = [c for c in (_bar(df, d, "Close") for d in dlist[:eidx]) if c]
    vols = [v for v in (_bar(df, d, "Volume") for d in dlist[:eidx]) if v]
    if len(closes) < 5 or not entry_open:
        return None

    chg5 = (entry_open - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0.0
    chg20 = (entry_open - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0.0

    rvol = None
    if len(vols) >= 3:
        ref = len(vols) - 1
        base_vols = vols[max(0, ref - 20):ref]
        base = sum(base_vols) / len(base_vols) if base_vols else 0.0
        rvol = round(vols[ref] / base, 2) if base else None

    atr_pct = None
    hi = [_bar(df, d, "High") for d in dlist[:eidx]]
    lo = [_bar(df, d, "Low") for d in dlist[:eidx]]
    trs = []
    for i in range(1, len(closes)):
        h, l, pc = hi[i], lo[i], closes[i - 1]
        if h and l and pc:
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) >= 14:
        atr_val = sum(trs[-14:]) / 14
        atr_pct = round(atr_val / entry_open * 100, 2) if entry_open else None

    avg_dollar_vol = None
    if len(closes) >= 5 and len(vols) >= 5:
        dv = [c * v for c, v in zip(closes[-20:], vols[-20:])]
        avg_dollar_vol = sum(dv) / len(dv)

    rctx = {
        "reaction_pct": reaction_pct,
        "drift_pct": 0.0,          # just opened — no drift visible yet
        "gap_pct": reaction_pct,   # open-vs-baseline IS the reaction at the open
        "implied_move_pct": None,
        "change_today": reaction_pct,
        "change_5d": chg5,
        "change_20d": chg20,
        "rvol": rvol,
        "post_vol_ratio": rvol,
        "atr_pct": atr_pct,
        "sector_change_pct": None,
        "market_cap": None,
        "avg_dollar_vol": avg_dollar_vol,
        "spread_pct": None,
        "short_float_pct": None,
        "days_to_cover": None,
    }
    return qs.compute_composite(rctx, weights)


def backtest_drift(days: int = 30, horizon: int = 1, max_symbols: int = 500) -> list:
    """Replay past reports → per-report {session, direction, reaction_pct, gated,
    alpha, ret, spy}. Returns the list of trades (empty if nothing to test).

    Bounded: only the `max_symbols` most RECENT reports get history downloaded (a
    full earnings-season window is thousands of tickers — yfinance rate-limits and
    a small VM can't download them all in reasonable time)."""
    import pandas as pd

    from alphadesk.config import FRICTION_BPS_PER_SIDE, MATERIAL_REACTION_PCT
    from alphadesk.ledger import store

    rows = [r for r in store.earnings_window(days_back=days, days_fwd=0)
            if r.get("session") in ("BMO", "AMC", "DAY") and r.get("symbol")]
    if not rows:
        return []
    # most recent first, bounded to max_symbols unique symbols
    rows.sort(key=lambda r: r.get("report_date", ""), reverse=True)
    seen: set = set()
    kept = []
    for r in rows:
        if r["symbol"].upper() in seen:
            continue
        seen.add(r["symbol"].upper())
        kept.append(r)
        if len(kept) >= max_symbols:
            break

    hist = _load_hist([r["symbol"] for r in kept] + ["SPY"])
    spy = hist.get("SPY")
    if spy is None:
        return []

    trades = []
    for r in rows:
        try:
            sym = r["symbol"].upper()
            df = hist.get(sym)
            if df is None:
                continue
            dlist = _days(df)
            rd = pd.Timestamp(r["report_date"]).normalize()
            sess = r.get("session")

            # baseline + entry day (BMO/DAY: prior close → report-day open;
            # AMC: report-day close → next trading day open)
            prior = [d for d in dlist if d < rd]
            if sess == "AMC":
                later = [d for d in dlist if d > rd]
                if not later:
                    continue
                entry_day = later[0]
                baseline = _bar(df, rd, "Close")
            else:
                if not prior:
                    continue
                baseline = _bar(df, prior[-1], "Close")
                entry_day = rd if rd in dlist else next((d for d in dlist if d > rd), None)
            if baseline is None or entry_day is None:
                continue
            entry_open = _bar(df, entry_day, "Open")
            if not entry_open:
                continue

            reaction_pct = (entry_open - baseline) / baseline * 100
            direction = "LONG" if reaction_pct >= 0 else "SHORT"
            gated = abs(reaction_pct) >= MATERIAL_REACTION_PCT

            # forward window: entry open → close `horizon` trading days later
            eidx = dlist.index(entry_day)
            exit_days = dlist[eidx + 1:]
            if len(exit_days) < horizon:
                continue
            exit_day = exit_days[horizon - 1]
            exit_close = _bar(df, exit_day, "Close")
            spy_en = _bar(spy, entry_day, "Open")
            spy_ex = _bar(spy, exit_day, "Close")
            if not (exit_close and spy_en and spy_ex):
                continue

            sign = 1.0 if direction == "LONG" else -1.0
            ret = sign * (exit_close - entry_open) / entry_open * 100
            spy_ret = sign * (spy_ex - spy_en) / spy_en * 100
            friction = 2 * FRICTION_BPS_PER_SIDE / 100
            trades.append({
                "session": sess, "direction": direction,
                "reaction_pct": round(reaction_pct, 2),
                "gated": gated, "alpha": round(ret - spy_ret - friction, 3),
                "ret": round(ret, 3), "spy": round(spy_ret, 3),
            })
        except Exception:
            continue
    return trades


def backtest_selection(days: int = 60, horizon: int = 1, max_symbols: int = 1500) -> list:
    """The SELECTION test — do the quant composite scores pick names that pay?

    Grades each historical candidate in the COMPOSITE's direction (what the desk
    actually trades), not the raw reaction direction, and buckets forward alpha by
    the composite score. If high-score names pay, the quant filter adds value even
    though the raw reaction thesis doesn't; if score doesn't sort forward alpha,
    the whole selection is noise.

    Composite is approximated from daily bars at the decision moment (reaction,
    rvol, ATR, dollar-volume); live-only inputs contribute 0."""
    import pandas as pd

    from alphadesk.config import FRICTION_BPS_PER_SIDE
    from alphadesk.ledger import store
    from alphadesk.quant import calibrate as qc

    weights = qc.load_weights()

    rows = [r for r in store.earnings_window(days_back=days, days_fwd=0)
            if r.get("session") in ("BMO", "AMC", "DAY") and r.get("symbol")]
    if not rows:
        return []
    rows.sort(key=lambda r: r.get("report_date", ""), reverse=True)
    seen: set = set()
    kept = []
    for r in rows:
        if r["symbol"].upper() in seen:
            continue
        seen.add(r["symbol"].upper())
        kept.append(r)
        if len(kept) >= max_symbols:
            break

    hist = _load_hist([r["symbol"] for r in kept] + ["SPY"])
    spy = hist.get("SPY")
    if spy is None:
        return []

    trades = []
    for r in kept:
        try:
            sym = r["symbol"].upper()
            df = hist.get(sym)
            if df is None:
                continue
            dlist = _days(df)
            rd = pd.Timestamp(r["report_date"]).normalize()
            sess = r.get("session")

            prior = [d for d in dlist if d < rd]
            if sess == "AMC":
                later = [d for d in dlist if d > rd]
                if not later:
                    continue
                entry_day = later[0]
                baseline = _bar(df, rd, "Close")
            else:
                if not prior:
                    continue
                baseline = _bar(df, prior[-1], "Close")
                entry_day = rd if rd in dlist else next((d for d in dlist if d > rd), None)
            if baseline is None or entry_day is None:
                continue
            entry_open = _bar(df, entry_day, "Open")
            if not entry_open:
                continue
            reaction_pct = (entry_open - baseline) / baseline * 100

            comp = _composite(df, dlist, entry_day, entry_open, baseline, reaction_pct, weights)
            if comp is None:
                continue
            direction = comp["direction"]
            score = comp["score"]

            eidx = dlist.index(entry_day)
            exit_days = dlist[eidx + 1:]
            if len(exit_days) < horizon:
                continue
            exit_day = exit_days[horizon - 1]
            exit_close = _bar(df, exit_day, "Close")
            spy_en = _bar(spy, entry_day, "Open")
            spy_ex = _bar(spy, exit_day, "Close")
            if not (exit_close and spy_en and spy_ex):
                continue

            sign = 1.0 if direction == "LONG" else -1.0
            ret = sign * (exit_close - entry_open) / entry_open * 100
            spy_ret = sign * (spy_ex - spy_en) / spy_en * 100
            friction = 2 * FRICTION_BPS_PER_SIDE / 100
            trades.append({
                "session": sess, "direction": direction,
                "reaction_pct": round(reaction_pct, 2),
                "score": round(score, 1),
                "selected": score >= _PREFILTER,
                "alpha": round(ret - spy_ret - friction, 3),
            })
        except Exception:
            continue
    return trades


_PREFILTER = 5.0   # the desk's QUANT_PREFILTER_MIN_SCORE


def _print_table(title: str, ts: list[dict]) -> None:
    if not ts:
        print(f"  {title}: (none)")
        return
    alphas = sorted(t["alpha"] for t in ts)
    n = len(alphas)
    mean = sum(alphas) / n
    median = alphas[n // 2]
    win = 100.0 * sum(1 for t in ts if t["alpha"] > 0) / n
    print(f"  {title:18} n={n:>4}  mean α={mean:+8.2f}%  med α={median:+8.2f}%  win={win:5.1f}%")


def report(trades: list[dict]) -> None:
    from alphadesk.config import MATERIAL_REACTION_PCT

    if not trades:
        print("\nNo past reports to test — run `earnings` first (refreshes the calendar),")
        print("or use a larger --days window.")
        return
    gated = [t for t in trades if t["gated"]]
    dropped = [t for t in trades if not t["gated"]]

    print("\n=== post-earnings drift backtest — forward alpha vs SPY (net friction) ===")
    print(f"  {len(trades)} reports · gate keeps |reaction| ≥ {MATERIAL_REACTION_PCT}%")
    print()
    _print_table("ALL reports", trades)
    _print_table("gated (traded)", gated)
    _print_table("gate-dropped", dropped)
    print()
    print("  by reaction bucket (gated arm):")
    print(f"  {'bucket':10} {'n':>4} {'mean α':>9} {'win%':>6}")
    for i, label in enumerate(BUCKET_LABELS):
        ts = [t for t in gated if _bucket(abs(t["reaction_pct"])) == i]
        if ts:
            _print_table(label, ts)
        else:
            print(f"  {label:10} {0:>4}")
    print()
    print("  by session:")
    for s in ("BMO", "AMC", "DAY"):
        _print_table(s, [t for t in gated if t["session"] == s])
    print()
    print("  by direction:")
    for d in ("LONG", "SHORT"):
        _print_table(d, [t for t in gated if t["direction"] == d])
    print("\n  α>0 means the reaction direction beat SPY after friction — a positive gate")
    print("  arm says the drift edge is real; a negative one says it is not (yet).")


def report_selection(trades: list[dict]) -> None:
    if not trades:
        print("\nNo past reports to test.")
        return
    sel = [t for t in trades if t["selected"]]
    rej = [t for t in trades if not t["selected"]]

    print("\n=== composite selection backtest — forward alpha by quant score ===")
    print(f"  {len(trades)} reports · graded in the COMPOSITE's direction (what the desk trades)")
    print()
    _print_table("ALL scored", trades)
    _print_table("selected (score≥5)", sel)
    _print_table("pre-filtered (<5)", rej)
    print()
    print("  by score bucket:")
    print(f"  {'score':10} {'n':>4} {'mean α':>9} {'win%':>6}")
    for lo, hi, label in ((0, 5, "<5"), (5, 10, "5-10"), (10, 20, "10-20"), (20, 100, ">20")):
        ts = [t for t in trades if lo <= t["score"] < hi]
        _print_table(label, ts)
    print()
    print("  by direction (selected arm):")
    for d in ("LONG", "SHORT"):
        _print_table(d, [t for t in sel if t["direction"] == d])
    print("\n  If selected > pre-filtered and higher score buckets pay more, the quant")
    print("  filter is selecting real alpha even though the raw reaction thesis is flat.")
