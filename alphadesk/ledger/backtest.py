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

    Chunked (~40 tickers per yfinance call): a full window can be 2000+ reporters,
    and a single threads=True download of that many spawns a thread per ticker and
    dies with 'can't start new thread' on a small VM."""
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
            if df is None:
                continue
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
            continue
    return out


def _days(df) -> list:
    return list(df.index.normalize().unique())


def _bar(df, day, col) -> float | None:
    try:
        rows = df.loc[df.index.normalize() == day]
        return float(rows[col].iloc[0]) if len(rows) else None
    except Exception:
        return None


def backtest_drift(days: int = 90, horizon: int = 1) -> list:
    """Replay past reports → per-report {session, direction, reaction_pct, gated,
    alpha, ret, spy}. Returns the list of trades (empty if nothing to test)."""
    import pandas as pd

    from alphadesk.config import FRICTION_BPS_PER_SIDE, MATERIAL_REACTION_PCT
    from alphadesk.ledger import store

    rows = [r for r in store.earnings_window(days_back=days, days_fwd=0)
            if r.get("session") in ("BMO", "AMC", "DAY") and r.get("symbol")]
    if not rows:
        return []

    hist = _load_hist([r["symbol"] for r in rows] + ["SPY"])
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
