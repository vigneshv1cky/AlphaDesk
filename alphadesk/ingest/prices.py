"""Price data — lazy, per-symbol, TTL-cached. Nothing here sweeps or polls.

Every function answers a factual question about a symbol the reader is already
looking at: what is it trading at, what do the intraday bars look like, how
liquid is it, what are its fundamentals. Fetching is driven by the request
path, so an idle terminal makes no market-data calls.

The one non-obvious responsibility is `_coverage_stats`: on a sparse feed a
"1-minute" series can be a handful of prints stretched across days, and it
renders identically to a real one. Anything that DISPLAYS an indicator has to
surface `indicators_reliable` rather than draw regardless.
"""

import logging
import math
import os
import threading
import time
from typing import Any, Optional

from alphadesk.net import bound_timeout as _bound
from alphadesk.config import (
    LOW_LIQUIDITY_DOLLAR_VOL,
    company_name,
    OWNERSHIP_TTL_S,
    RSI_CROSS_OVERBOUGHT,
    RSI_CROSS_OVERSOLD,
    now_et,
)

log = logging.getLogger("alphadesk.prices")

_TTL_S = 120
_CACHE_MAX_ENTRIES = 2000
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _evict_expired(cache: dict[str, tuple[float, Any]], ttl_s: float):
    # `Any` rather than `object` for the value: dict is invariant, so an
    # `object` annotation rejects every real cache here (each holds a more
    # specific value type) even though eviction only ever reads v[0].
    now = time.time()
    stale = [k for k, v in cache.items() if now - v[0] > ttl_s * 2]
    for k in stale:
        del cache[k]

_alpaca_client: Any = None
_alpaca_client_lock = threading.Lock()


def _alpaca_data_client():
    """Lazily-built, process-wide Alpaca market-data client (paper keys fine).
    Returns None if keys are missing or the SDK can't initialise."""
    global _alpaca_client
    if _alpaca_client is None:
        with _alpaca_client_lock:
            if _alpaca_client is None:
                try:
                    import os
                    from alpaca.data.historical import StockHistoricalDataClient
                    _alpaca_client = _bound(StockHistoricalDataClient(
                        os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]))
                except Exception as exc:      # missing keys / import failure
                    log.debug("alpaca data client unavailable: %s", exc)
                    return None
    return _alpaca_client


def _live_last_trade(symbol: str) -> Optional[tuple[float, object]]:
    """Real-time last trade for ONE symbol from Alpaca → (price, timestamp) or None.
    Deliberately has NO yfinance fallback — get_context owns that. The timestamp
    lets the caller gate PRE/AFTER fills: no trade in the current extended session
    means the stock can't actually fill, so treat it like a CLOSED pick."""
    client = _alpaca_data_client()
    if client is None:
        return None
    try:
        from alpaca.data.requests import StockLatestTradeRequest
        trades = client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=[symbol.upper()]))
        t = trades.get(symbol.upper())
        if t and t.price:
            return (round(float(t.price), 4), getattr(t, "timestamp", None))
        return None
    except Exception as exc:
        log.debug("live last-trade failed %s: %s", symbol, exc)
        return None


# Module-level caches and lookup tables used by the functions below. These live
# here rather than beside their functions because a refactor that deletes a
# function must not be able to take shared state with it — which is exactly how
# they were lost once.
_liquidity_batch_cache: dict[str, Any] = {"ts": 0.0, "key": None, "data": {}}

_macro_cache: dict | None = None
_macro_cache_lock = threading.Lock()
_macro_cache_ts: float = 0.0
_macro_prev: dict[str, float] = {}
_MACRO_CACHE_TTL_S = 600  # 10 min — macro data moves slowly

_SECTOR_MAP = {
    "Technology": "XLK", "Financial Services": "XLF", "Energy": "XLE",
    "Healthcare": "XLV", "Industrials": "XLI", "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP", "Basic Materials": "XLB",
    "Real Estate": "XLRE", "Utilities": "XLU",
    "Communication Services": "XLC",
}
_sector_cache: dict[str, float] = {}
_sector_cache_ts: float = 0.0
_SECTOR_TTL_S = 300


def get_context(symbol: str) -> Optional[dict]:
    """Price/liquidity context for one symbol (fetched on demand, cached)."""
    sym = symbol.upper()
    with _cache_lock:
        hit = _cache.get(sym)
        if hit and time.time() - hit[0] < _TTL_S:
            return hit[1]
    try:
        import yfinance as yf
        df = yf.Ticker(sym).history(period="90d", interval="1d")
        if df is None or len(df) < 5:
            return None
        closes = df["Close"].astype(float)
        vols = df["Volume"].astype(float)
        daily_last = float(closes.iloc[-1])
        daily_prev = float(closes.iloc[-2])
        # yfinance can hand back a NaN Close (a halted stock, a data-feed gap
        # right around an earnings print — exactly this engine's population).
        # bool(nan) is True in Python, so downstream `if x else ...` guards do
        # NOT catch it — it silently passes through as a "valid" price unless
        # caught here. No live/daily price to anchor on means no reliable
        # context at all, so bail the same way the len(df) < 5 check above does.
        if not (math.isfinite(daily_last) and math.isfinite(daily_prev)):
            return None
        latest_is_today = df.index[-1].date() == now_et().date() and len(closes) > 1
        # Prefer a REAL-TIME last trade over yfinance's latest daily close (which
        # is stale/pre-gap the morning after earnings). When live is available,
        # compare it against the last COMPLETED session (skip a partial today bar)
        # so change_today is the true move, not 0%. No live price → old behaviour.
        rt = _live_last_trade(sym)
        rt_price, rt_ts = (rt[0], rt[1]) if rt else (None, None)
        if rt_price and math.isfinite(rt_price):
            last = rt_price
            prev = daily_prev if latest_is_today else daily_last
        else:
            last = daily_last
            prev = daily_prev
        avg_dollar_vol = float((closes * vols).tail(20).mean())
        if not math.isfinite(avg_dollar_vol):
            avg_dollar_vol = 0.0   # unmeasurable — treat as illiquid (fail closed
                                   # on the low_liquidity gate below), not as "0
                                   # is fine, let it through" via a stray NaN.
        # Relative volume: the last COMPLETED session's volume vs its own recent
        # norm — a confirmation/participation fact (is the news being acted on, or
        # ignored?). We skip an in-progress bar: intraday, yfinance's latest daily
        # bar is partial, so partial ÷ full-day norm reads misleadingly low for
        # every name. Reference the prior completed session instead; baseline is the
        # 20 sessions before it. Evidence the agents weigh, never a code threshold.
        n = len(vols)
        ref = n - 1
        if df.index[-1].date() == now_et().date() and n > 1:
            ref = n - 2   # current bar is live/partial — use the last closed session
        base_vols = vols.iloc[max(0, ref - 20):ref]
        base_vol = float(base_vols.mean()) if len(base_vols) else 0.0
        ref_vol = float(vols.iloc[ref])
        rvol = (round(ref_vol / base_vol, 2)
                if base_vol and math.isfinite(base_vol) and math.isfinite(ref_vol) else None)
        # ATR (14-day): the stock's typical daily range as % of price. Used by
        # quant signals to judge whether a move is routine or extraordinary, and
        # by the watcher for volatility-scaled stop distances.
        atr_pct: float | None = None
        try:
            hi = df["High"].astype(float)
            lo = df["Low"].astype(float)
            prev_c = closes.shift(1)
            tr1 = hi - lo
            tr2 = (hi - prev_c).abs()
            tr3 = (lo - prev_c).abs()
            true_range = tr1.combine(tr2, max).combine(tr3, max)
            atr_val = float(true_range.tail(14).mean())
            # atr_val and last alone don't catch NaN — bool(nan) is True in Python.
            atr_pct = (round(atr_val / last * 100, 2)
                       if atr_val and last and math.isfinite(atr_val) else None)
        except Exception:
            pass

        def _pct_change(ref_idx: int) -> float:
            if len(closes) <= abs(ref_idx):
                return 0.0
            ref_close = float(closes.iloc[ref_idx])
            if not (math.isfinite(ref_close) and ref_close):
                return 0.0
            return round((last - ref_close) / ref_close * 100, 2)

        ctx = {
            "symbol": sym,
            "last_price": round(last, 4),
            "change_today_pct": round((last - prev) / prev * 100, 2) if prev else 0.0,
            "change_5d_pct": _pct_change(-6),
            "change_20d_pct": _pct_change(-21),
            "high_90d": round(float(closes.tail(63).max()), 2),
            "low_90d": round(float(closes.tail(63).min()), 2),
            "avg_dollar_vol": round(avg_dollar_vol),
            "rvol": rvol,          # latest-session volume ÷ its 20-session norm
            "low_liquidity": avg_dollar_vol < LOW_LIQUIDITY_DOLLAR_VOL,
            "closes_10d": [round(float(c), 2) for c in closes.tail(10)],
            "atr_pct": atr_pct,
            "last_trade_ts": rt_ts,  # None if no real-time Alpaca trade (stale yfinance only)
        }
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX_ENTRIES:
                _evict_expired(_cache, _TTL_S)
            _cache[sym] = (time.time(), ctx)
        return ctx
    except Exception as exc:
        log.debug("price context failed %s: %s", sym, exc)
        return None


_fund_cache: dict[str, tuple[float, dict | None]] = {}
_FUND_TTL_S = 3600


def get_fundamentals(symbol: str) -> Optional[dict]:
    """Basic valuation/quality facts (best-effort via yfinance; cached 1h)."""
    sym = symbol.upper()
    with _cache_lock:
        hit = _fund_cache.get(sym)
        if hit and time.time() - hit[0] < _FUND_TTL_S:
            return hit[1]
    out: dict | None = None
    try:
        import yfinance as yf
        info = yf.Ticker(sym).info or {}
        def _si(v):
            try:
                f = float(str(v).replace("%", ""))
                return round(f, 2) if f == f else None
            except (TypeError, ValueError):
                return None
        out = {
            "market_cap": info.get("marketCap"),
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "profit_margin": info.get("profitMargins"),
            "revenue_growth": info.get("revenueGrowth"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "short_float_pct": _si(info.get("shortPercentOfFloat")),
            "days_to_cover": _si(info.get("shortRatio")),
        }
        if not any(v is not None for v in out.values()):
            out = None
    except Exception as exc:
        log.debug("fundamentals failed %s: %s", sym, exc)
    with _cache_lock:
        if len(_fund_cache) >= _CACHE_MAX_ENTRIES:
            _evict_expired(_fund_cache, _FUND_TTL_S)  # type: ignore[arg-type]
        _fund_cache[sym] = (time.time(), out)
    return out


_ownership_cache: dict[str, tuple[float, dict | None]] = {}


def get_institutional_ownership(symbol: str) -> Optional[dict]:
    """Who holds this stock (best-effort via yfinance; cached — 13F/major-
    holder breakdowns move on a quarterly cadence, far slower than a quote).

    Deliberately yfinance, not OpenBB/SEC: SEC's Form 13F is filed BY an
    institutional manager and lists THEIR holdings across many companies —
    querying it by a company's own symbol (e.g. AAPL) returns nothing,
    because Apple isn't an institutional filer. There's no free reverse index
    from "held company" back to "who holds it" in raw SEC data; yfinance
    already aggregates that for free via Yahoo's own data pipeline."""
    sym = symbol.upper()
    with _cache_lock:
        hit = _ownership_cache.get(sym)
        if hit and time.time() - hit[0] < OWNERSHIP_TTL_S:
            return hit[1]
    out: dict | None = None
    try:
        import yfinance as yf
        tk = yf.Ticker(sym)
        major = tk.get_major_holders()
        inst = tk.get_institutional_holders()
        breakdown: dict = {}
        if major is not None and not major.empty:
            col = "Value" if "Value" in major.columns else major.columns[0]
            for label, value in major[col].items():
                breakdown[str(label)] = value
        holders = []
        if inst is not None and not inst.empty:
            for _, row in inst.head(10).iterrows():
                holders.append({
                    "holder": row.get("Holder"),
                    "shares": row.get("Shares"),
                    "value": row.get("Value"),
                    "pct_change": row.get("pctChange"),
                    "date_reported": str(row.get("Date Reported")) if row.get("Date Reported") is not None else None,
                })
        if breakdown or holders:
            out = {"breakdown": breakdown, "top_holders": holders}
    except Exception as exc:
        log.debug("institutional ownership failed %s: %s", sym, exc)
    with _cache_lock:
        if len(_ownership_cache) >= _CACHE_MAX_ENTRIES:
            _evict_expired(_ownership_cache, OWNERSHIP_TTL_S)  # type: ignore[arg-type]
        _ownership_cache[sym] = (time.time(), out)
    return out


_opt_cache: dict[str, tuple[float, dict | None]] = {}
_OPT_TTL_S = 900   # 15m — IV/expected-move drift slowly enough intraday


def _mid(row) -> Optional[float]:
    """Bid/ask midpoint, falling back to last trade; None if neither is usable."""
    bid = float(row.get("bid") or 0)
    ask = float(row.get("ask") or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    last = float(row.get("lastPrice") or 0)
    return last if last > 0 else None


def get_options_context(symbol: str) -> Optional[dict]:
    """Options-implied expected move + ATM IV — the market's own 'how much is
    already priced in' number, the quantitative anchor for the priced-in debate.

    Best-effort via yfinance, cached 15m, fail-open (None when a name has no
    options or the chain is too illiquid to trust). Pure facts — the agents weigh
    them; nothing here decides anything (design law #2). Two independent reads:
      • expected_move_to_expiry_pct — the ATM straddle mid ÷ spot: the market's
        actual quoted move to the nearest expiry (ground truth, no term-structure
        assumption).
      • expected_move_{1,5,10}d_pct — ATM IV projected over standard trading-day
        windows (sqrt-time), so the desk can match it to the pick's horizon.
    """
    sym = symbol.upper()
    with _cache_lock:
        hit = _opt_cache.get(sym)
        if hit and time.time() - hit[0] < _OPT_TTL_S:
            return hit[1]

    out: dict | None = None
    try:
        import pandas as pd
        import yfinance as yf

        ctx = get_context(sym)
        spot = float(ctx["last_price"]) if ctx and ctx.get("last_price") else 0.0
        if not spot:
            raise ValueError("no spot price")

        tk = yf.Ticker(sym)
        expiries = tk.options or ()
        if not expiries:
            raise ValueError("no listed options")

        # nearest expiry ≥2 calendar days out (skip 0-1 DTE gamma noise)
        today = now_et().date()
        exp, dte = None, 0
        for e in expiries:
            d = (pd.Timestamp(e).date() - today).days
            if d >= 2:
                exp, dte = e, d
                break
        if exp is None:  # only ultra-short expiries listed — take the furthest
            exp = expiries[-1]
            dte = max(1, (pd.Timestamp(exp).date() - today).days)

        chain = tk.option_chain(exp)
        calls, puts = chain.calls, chain.puts
        if calls.empty or puts.empty:
            raise ValueError("empty chain")

        call = calls.iloc[(calls["strike"] - spot).abs().argmin()]
        put = puts.iloc[(puts["strike"] - spot).abs().argmin()]

        ivs = [float(v) for v in (call.get("impliedVolatility"), put.get("impliedVolatility"))
               if v and float(v) > 0]
        atm_iv = sum(ivs) / len(ivs) if ivs else None   # decimal, annualized

        cm, pm = _mid(call), _mid(put)
        straddle = cm + pm if (cm and pm) else None
        em_expiry = round(straddle / spot * 100, 2) if straddle else None
        if em_expiry and em_expiry > 100:  # nonsense from a broken/illiquid quote
            em_expiry = None

        if atm_iv is None and em_expiry is None:
            raise ValueError("no usable IV or straddle")

        def _em_days(nd: int) -> float | None:
            return round(atm_iv * math.sqrt(nd / 252) * 100, 2) if atm_iv else None

        out = {
            "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
            "expiry": exp,
            "days_to_expiry": dte,
            "expected_move_to_expiry_pct": em_expiry,
            "expected_move_1d_pct": _em_days(1),
            "expected_move_5d_pct": _em_days(5),
            "expected_move_10d_pct": _em_days(10),
        }
    except Exception as exc:
        log.debug("options context failed %s: %s", sym, exc)
        out = None
    with _cache_lock:
        if len(_opt_cache) >= _CACHE_MAX_ENTRIES:
            _evict_expired(_opt_cache, _OPT_TTL_S)  # type: ignore[arg-type]
        _opt_cache[sym] = (time.time(), out)
    return out


_earn_ctx_cache: dict[str, tuple[float, dict | None]] = {}
_EARN_CTX_TTL_S = 3600


def get_earnings_context(symbol: str) -> Optional[dict]:
    """Code-fetched earnings FACTS for the debate's earnings brief — no LLM, zero
    confabulation surface: the company's recent beat/miss track record, quarterly
    revenue/income trend, and analyst estimate + revision direction (post-report
    revisions are literally the drift mechanism). Best-effort via yfinance, cached
    1h, None when nothing usable. Replaces what an LLM would otherwise narrate
    from memory (the COCO/TSLA confabulation cases)."""
    sym = symbol.upper()
    with _cache_lock:
        hit = _earn_ctx_cache.get(sym)
        if hit and time.time() - hit[0] < _EARN_CTX_TTL_S:
            return hit[1]

    def _num(v, nd=2):
        try:
            f = float(v)
            return round(f, nd) if f == f else None   # NaN != NaN
        except (TypeError, ValueError):
            return None

    out: dict = {}
    try:
        import yfinance as yf
        tk = yf.Ticker(sym)
        try:   # beat/miss TRACK RECORD (last 4 reported quarters)
            ed = tk.earnings_dates
            if ed is not None and not ed.empty:
                rep = ed.dropna(subset=["Reported EPS"]).head(4)
                hist = [{"date": str(getattr(d, "date", lambda: d)())[:10],
                         "eps_estimate": _num(r.get("EPS Estimate")),
                         "eps_actual": _num(r.get("Reported EPS")),
                         "surprise_pct": _num(r.get("Surprise(%)"))}
                        for d, r in rep.iterrows()]
                if hist:
                    beats = sum(1 for h in hist if (h["surprise_pct"] or 0) > 0)
                    out["report_history"] = hist
                    out["beat_streak"] = f"{beats}/{len(hist)} beats"
        except Exception:
            pass
        try:   # quarterly revenue / net income trend
            q = tk.quarterly_income_stmt
            if q is not None and not q.empty:
                if "Total Revenue" in q.index:
                    revs = [float(v) for v in q.loc["Total Revenue"].tolist()[:4] if v == v]
                    if len(revs) >= 2 and revs[1]:
                        out["revenue_qoq_pct"] = round((revs[0] - revs[1]) / abs(revs[1]) * 100, 2)
                        out["revenue_last4_bn"] = [round(v / 1e9, 2) for v in revs]
                if "Net Income" in q.index:
                    out["net_income_last4_bn"] = [
                        round(float(v) / 1e9, 2) for v in q.loc["Net Income"].tolist()[:4] if v == v]
        except Exception:
            pass
        try:   # analyst estimate trajectory + revisions (the drift fuel)
            t = tk.get_eps_trend()
            if t is not None and not t.empty and "current" in t.columns:
                r0 = t.iloc[0]
                cur, d30 = _num(r0.get("current")), _num(r0.get("30daysAgo"))
                if cur is not None:
                    out["next_q_eps_estimate"] = cur
                    if d30:
                        out["estimate_30d_change_pct"] = round((cur - d30) / abs(d30) * 100, 2)
        except Exception:
            pass
        try:
            rv = tk.get_eps_revisions()
            if rv is not None and not rv.empty:
                r0 = rv.iloc[0]
                out["revisions_30d"] = {"up": int(r0.get("upLast30days") or 0),
                                        "down": int(r0.get("downLast30days") or 0)}
        except Exception:
            pass
    except Exception as exc:
        log.debug("earnings context failed %s: %s", sym, exc)
    result = out or None
    with _cache_lock:
        _earn_ctx_cache[sym] = (time.time(), result)
    return result


_earn_move_cache: dict[str, Any] = {"ts": 0.0, "key": None, "data": {}}


def liquidity_batch(symbols: list[str], ttl: int = 3600) -> dict[str, bool]:
    """Same 20-day-avg-dollar-volume liquidity check as get_context()'s
    low_liquidity flag (the one the live trading pipeline actually gates on),
    but for many symbols in one batched download instead of one Ticker.history()
    call per symbol — get_context() at that scale would mean one live fetch per
    row on a page that can list 100+ names. Trading volume moves slowly day to
    day, so this caches for an hour rather than get_context()'s 2-minute TTL.
    Returns {symbol: low_liquidity}; a symbol absent from the result (unmeasurable)
    should be treated as unknown, not liquid.
    """
    import pandas as pd

    syms = sorted({s.upper() for s in symbols})
    key = repr(syms)
    now = time.time()
    with _cache_lock:
        c = _liquidity_batch_cache
        if c["key"] == key and now - c["ts"] < ttl:
            return c["data"]

    out: dict[str, bool] = {}
    if syms:
        try:
            import yfinance as yf
            df = yf.download(syms, period="30d", interval="1d", group_by="ticker",
                             progress=False, threads=True, auto_adjust=True)
            for sym in syms:
                try:
                    sub = (df[sym] if isinstance(df.columns, pd.MultiIndex)
                           and sym in df.columns.get_level_values(0) else df)
                    closes = sub["Close"].astype(float)
                    vols = sub["Volume"].astype(float)
                    dollar_vol = (closes * vols).dropna().tail(20)
                    if dollar_vol.empty:
                        continue
                    out[sym] = float(dollar_vol.mean()) < LOW_LIQUIDITY_DOLLAR_VOL
                except Exception:
                    continue
        except Exception as exc:
            log.debug("liquidity batch download failed: %s", exc)

    with _cache_lock:
        _liquidity_batch_cache.update(ts=now, key=key, data=out)
    return out


def intraday_bars(symbol: str, start, interval: str = "1m") -> list[dict]:
    """Minute bars for `symbol` from `start` (a tz-aware datetime) to now, via Alpaca
    (free IEX feed). The bar series behind get_chart_series() and the coverage
    statistics that gate its indicators. Chronological (oldest first); empty list on
    any failure, which the caller renders as "no intraday bars" rather than an error."""
    client = _alpaca_data_client()
    if client is None:
        return []
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        spec = CHART_INTERVALS.get(interval, CHART_INTERVALS["1m"])
        tf = TimeFrame(spec["n"], TimeFrameUnit(spec["unit"]))
        resp = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbol.upper(), timeframe=tf,
            start=start, feed=DataFeed.IEX))
        data = resp.data.get(symbol.upper(), []) if hasattr(resp, "data") else []
        bars = [{"ts": b.timestamp, "open": float(b.open), "high": float(b.high),
                 "low": float(b.low), "close": float(b.close),
                 "volume": float(getattr(b, "volume", 0) or 0)} for b in data]
        bars.sort(key=lambda x: x["ts"])
        return bars
    except Exception as exc:
        log.debug("intraday_bars failed for %s: %s", symbol, exc)
        return []


# A full US regular session is 390 one-minute bars. The IEX feed prints far
# fewer for illiquid names, so this ratio is the honest measure of whether a
# "1-minute" indicator is really computed on 1-minute data.
BARS_PER_SESSION = 390


def _coverage_stats(bars: list[dict]) -> dict:
    """How real is this "1-minute" series? Returns bar count, sessions
    spanned, the fraction of a full session actually present, the median
    intraday gap, and a single indicators_reliable verdict for the UI.

    Exists because the IEX feed's sparsity is invisible on a rendered chart:
    92 bars stretched across 5 sessions draws exactly like 1950 real ones.
    """
    from alphadesk.config import ET, CHART_MAX_MEDIAN_GAP_MIN, CHART_MIN_COVERAGE
    n = len(bars)
    if n < 2:
        return {"bar_count": n, "sessions": 0, "coverage": 0.0,
                "median_gap_min": None, "indicators_reliable": False}
    sessions = len({b["ts"].astimezone(ET).date() for b in bars})
    gaps = sorted(g for g in
                  ((bars[i]["ts"] - bars[i - 1]["ts"]).total_seconds() / 60
                   for i in range(1, n))
                  if g < 240)          # drop overnight/weekend gaps
    median_gap = gaps[len(gaps) // 2] if gaps else None
    coverage = round(n / (sessions * BARS_PER_SESSION), 3) if sessions else 0.0
    reliable = bool(coverage >= CHART_MIN_COVERAGE
                    and median_gap is not None
                    and median_gap <= CHART_MAX_MEDIAN_GAP_MIN)
    # "bar_count", not "bars" — get_chart_series() merges this dict alongside
    # its OHLC array, which owns the "bars" key.
    return {"bar_count": n, "sessions": sessions, "coverage": coverage,
            "median_gap_min": round(median_gap, 1) if median_gap is not None else None,
            "indicators_reliable": reliable}


_chart_cache: dict[str, tuple[float, Optional[dict]]] = {}
_CHART_TTL_S = 30


def daily_bars(symbol: str, period: str, interval: str = "1d") -> list[dict]:
    """Daily OHLCV via yfinance, for ranges past the minute feed's reach.

    Alpaca's minute bars stop at ~30 days, so 3M and beyond need a different
    series entirely rather than a longer request. yfinance periods are its own
    vocabulary ("3mo", "ytd", "max"), passed straight through.

    Empty list on any failure — the caller renders "no bars" rather than a
    half-drawn chart.
    """
    try:
        import yfinance as yf
        yf_interval = CHART_INTERVALS.get(interval, {}).get("yf", "1d")
        df = yf.Ticker(symbol.upper()).history(period=period, interval=yf_interval)
        if df is None or df.empty:
            return []
        out = []
        for ts, row in df.iterrows():
            out.append({
                "ts": ts.to_pydatetime(),
                "open": float(row["Open"]), "high": float(row["High"]),
                "low": float(row["Low"]), "close": float(row["Close"]),
                "volume": float(row.get("Volume") or 0),
            })
        return out
    except Exception as exc:
        log.debug("daily bars failed for %s (%s): %s", symbol, period, exc)
        return []


# Bar intervals the chart offers. `unit` is Alpaca's; `yf` is the yfinance
# equivalent for the ones that outrun the minute feed. `max_days` is how far
# back that interval can actually be fetched — Alpaca thins minute history and
# yfinance caps intraday at 60 days, so asking for 1-minute bars across five
# years is not a slow request, it is an impossible one.
CHART_INTERVALS: dict[str, dict] = {
    "1m":  {"n": 1,  "unit": "Min",  "max_days": 30,   "label": "1 min"},
    "2m":  {"n": 2,  "unit": "Min",  "max_days": 30,   "label": "2 mins"},
    "5m":  {"n": 5,  "unit": "Min",  "max_days": 60,   "label": "5 mins"},
    "15m": {"n": 15, "unit": "Min",  "max_days": 60,   "label": "15 mins"},
    "30m": {"n": 30, "unit": "Min",  "max_days": 60,   "label": "30 mins"},
    "1h":  {"n": 1,  "unit": "Hour", "max_days": 730,  "label": "1 hour"},
    "4h":  {"n": 4,  "unit": "Hour", "max_days": 730,  "label": "4 hours"},
    "1d":  {"n": 1,  "unit": "Day",  "max_days": None, "yf": "1d",  "label": "1 day"},
    "1wk": {"n": 1,  "unit": "Week", "max_days": None, "yf": "1wk", "label": "1 week"},
    "1mo": {"n": 1,  "unit": "Month","max_days": None, "yf": "1mo", "label": "1 month"},
}

# Roughly how many calendar days each range spans, for deciding whether a
# requested interval can actually cover it.
RANGE_DAYS = {"1D": 1, "5D": 5, "1M": 31, "3M": 93, "6M": 186,
              "YTD": 365, "1Y": 365, "5Y": 1825, "MAX": 20000}


# Sub-daily bars come off Alpaca's intraday feed, and the cost of that fetch
# scales with the SPAN asked for, not with the bars returned. Measured against
# a warm server: 3M of hourly is 0.7s, 6M is 3.5s, and YTD/1Y are 9-14s — and
# a year of hourly is 2,031 points in a 449px tile, 4.5 bars to the pixel, so
# it draws indistinguishably from the daily series that answers in 5ms. The
# terminal was offering all ten intervals for every range and charging seconds
# for resolution the screen cannot show.
#
# 3M is the cut: the last span where intraday is both quick and legible.
INTRADAY_MAX_OFFER_DAYS = 93

# Below this an "interval" is a handful of points, not a chart — 4-hour bars
# over one day is a couple of them, and a monthly bar over a year is twelve.
#
# 13 rather than a rounder number because it sits in a real gap: monthly over
# a year estimates 12 and is genuinely too thin to read, while weekly over a
# quarter estimates 13.3 and is a usable chart. The threshold is calibrated to
# the menu it governs, not chosen for tidiness.
_MIN_OFFERABLE_BARS = 13

# Intraday bars are fetched over `span + 3` CALENDAR days (see the window built
# in get_chart_series), and the feed prints roughly an eight-hour day once
# extended hours are counted. Two-thirds of calendar days are trading days.
#
# Estimating from RANGE_DAYS alone was wrong in a way that mattered: it read
# the 1D range as one day when the fetch actually covers four, so 1D/1m came
# out as 390 bars against 1,553 served and 30-minute bars looked too sparse to
# offer when they are ~50. Checked against measured counts, this lands within a
# few percent on hourly at every range (1Y: estimates 2,031, serves 2,031).
_INTRADAY_LOOKBACK_PAD_DAYS = 3
_FEED_MINUTES_PER_SESSION = 480
_TRADING_DAY_RATIO = 252 / 365


def _interval_minutes(interval: str) -> Optional[int]:
    """Bar length in minutes, or None for daily and coarser."""
    spec = CHART_INTERVALS.get(interval)
    if not spec:
        return None
    unit = str(spec["unit"])
    if unit == "Min":
        return int(spec["n"])
    if unit == "Hour":
        return int(spec["n"]) * 60
    return None


def _estimated_bars(span_days: int, interval: str) -> float:
    mins = _interval_minutes(interval)
    if mins:
        window = span_days + _INTRADAY_LOOKBACK_PAD_DAYS
        return window * _TRADING_DAY_RATIO * (_FEED_MINUTES_PER_SESSION / mins)
    if interval == "1d":
        return span_days * _TRADING_DAY_RATIO   # trading days, not calendar days
    if interval == "1wk":
        return span_days / 7
    return span_days / 30.4                     # 1mo


def available_intervals(range_key: str) -> list[str]:
    """Which intervals this range should actually OFFER.

    The policy lives here rather than in the UI for the same reason CHART_RANGES
    does: the two must not be able to disagree about what "1Y" can serve. The
    UI renders this list and nothing else, so a combination that would be
    downgraded, or that would cost seconds to draw as an unreadable smear, is
    never presented rather than merely regretted afterwards.
    """
    span = RANGE_DAYS.get((range_key or "1D").upper(), 1)
    out = []
    for key in CHART_INTERVALS:
        intraday = _interval_minutes(key) is not None
        # Never offer something that would come back as a different series.
        if resolve_interval(range_key, key) != key:
            continue
        if intraday and span > INTRADAY_MAX_OFFER_DAYS:
            continue
        if _estimated_bars(span, key) < _MIN_OFFERABLE_BARS:
            continue
        out.append(key)
    return out


def resolve_interval(range_key: str, wanted: str | None) -> str:
    """The finest interval that can actually cover `range_key`.

    Returned rather than enforced silently: the caller reports what it got, so
    a reader who asked for 1-minute bars over a year can see they were served
    daily instead of quietly believing the chart is minute data.
    """
    span = RANGE_DAYS.get((range_key or "1D").upper(), 1)
    want = (wanted or "").lower()
    if want not in CHART_INTERVALS:
        # No preference: minute bars for a day or a week, daily beyond.
        return "1m" if span <= 5 else "1d"
    cap = CHART_INTERVALS[want]["max_days"]
    if cap is not None and span > cap:
        for key in ("1h", "4h", "1d", "1wk", "1mo"):
            c = CHART_INTERVALS[key]["max_days"]
            if c is None or span <= c:
                return key
        return "1mo"
    return want


# The ranges the UI offers, mapped to how each is actually sourced. Kept here
# rather than in the UI so the two can never disagree about what "3M" means.
CHART_RANGES: dict[str, dict] = {
    "1D":  {"kind": "intraday", "days": 1},
    "5D":  {"kind": "intraday", "days": 5},
    "1M":  {"kind": "daily", "period": "1mo"},
    "3M":  {"kind": "daily", "period": "3mo"},
    "6M":  {"kind": "daily", "period": "6mo"},
    "YTD": {"kind": "daily", "period": "ytd"},
    "1Y":  {"kind": "daily", "period": "1y"},
    "5Y":  {"kind": "daily", "period": "5y"},
    "MAX": {"kind": "daily", "period": "max"},
}


def _daily_coverage(bars: list[dict]) -> dict:
    """Coverage for a DAILY series.

    The intraday gate asks "did the feed print in most minutes", which is
    meaningless here — a daily bar per trading day is complete by construction,
    so a daily series is as good as this feed gets.

    It used to also fail the whole series below 35 bars, MACD's 26+9 warm-up.
    That is a real limit but it is MACD's, and applying it to everything hid
    RSI-9 from a 24-bar month that could support it perfectly well — switching
    range to 1M silently dropped every pane the reader had chosen. Warm-up is
    per indicator and is judged where each one is drawn (PANE_INDICATORS'
    minBars); what this flag answers is the question it was invented for,
    whether the FEED can be trusted.
    """
    n = len(bars)
    return {"bar_count": n, "sessions": n, "coverage": 1.0 if n else 0.0,
            "median_gap_min": None, "indicators_reliable": n > 0}


def get_chart_series(symbol: str, days: int = 2, range_key: str | None = None,
                     interval: str | None = None) -> Optional[dict]:
    """OHLC + full RSI-9 and MACD(12,26,9) SERIES for the human chart.

    Indicators are computed over the whole fetched window but only marked
    reliable via _coverage_stats — see CHART_MIN_COVERAGE. The caller is
    expected to surface that, not silently draw.
    """
    sym = symbol.upper()
    spec = CHART_RANGES.get((range_key or "").upper())
    used = resolve_interval(range_key or "1D", interval)
    key = f"{sym}:{range_key or days}:{used}"
    with _cache_lock:
        hit = _chart_cache.get(key)
        if hit and time.time() - hit[0] < _CHART_TTL_S:
            return hit[1]
    result: Optional[dict] = None
    try:
        from datetime import timedelta
        # The INTERVAL decides the feed, not the range: hour bars over three
        # months still come from Alpaca, and daily bars over five days still
        # come from yfinance if that is what was asked for.
        if CHART_INTERVALS[used].get("yf"):
            period = spec["period"] if spec and spec["kind"] == "daily" else f"{max(1, spec['days'] if spec else days)}d"
            bars = daily_bars(sym, period, used)
            stats = _daily_coverage(bars)
        else:
            span = RANGE_DAYS.get((range_key or "1D").upper(), days)
            bars = intraday_bars(sym, now_et() - timedelta(days=span + 3), used)
            # The coverage gate measures MINUTE density. It only means anything
            # for minute bars; an hourly series is complete by construction the
            # same way a daily one is.
            stats = None if used in ("1m", "2m") else _daily_coverage(bars)
        if len(bars) >= 2:
            import pandas as pd
            closes = pd.Series([b["close"] for b in bars])

            delta = closes.diff()
            avg_gain = delta.clip(lower=0).ewm(alpha=1 / 9, min_periods=9, adjust=False).mean()
            avg_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 9, min_periods=9, adjust=False).mean()
            rs = avg_gain / avg_loss.where(avg_loss != 0, other=float("nan"))
            rsi = (100 - 100 / (1 + rs)).where(avg_loss != 0, other=100.0)

            macd_line = (closes.ewm(span=12, adjust=False).mean()
                         - closes.ewm(span=26, adjust=False).mean())
            signal_line = macd_line.ewm(span=9, adjust=False).mean()

            def _pt(v):
                f = float(v)
                return round(f, 4) if math.isfinite(f) else None

            result = {
                "symbol": sym,
                "bars": [{"t": b["ts"].isoformat(), "o": b["open"], "h": b["high"],
                          "l": b["low"], "c": b["close"], "v": b.get("volume", 0.0)}
                         for b in bars],
                "rsi_9": [_pt(v) for v in rsi],
                "macd": [_pt(v) for v in macd_line],
                "macd_signal": [_pt(v) for v in signal_line],
                "macd_hist": [_pt(a - b) for a, b in zip(macd_line, signal_line)],
                "thresholds": {"rsi_oversold": RSI_CROSS_OVERSOLD,
                               "rsi_overbought": RSI_CROSS_OVERBOUGHT},
                "interval": used,
                "interval_label": CHART_INTERVALS[used]["label"],
                "interval_requested": (interval or "").lower() or None,
                "range": (range_key or "").upper() or None,
                # What the toolbar may offer for THIS range. Travels with the
                # series so the client never has to hold a copy of the policy.
                "intervals": available_intervals(range_key or "1D"),
                **(stats if stats is not None else _coverage_stats(bars)),
            }
    except Exception as exc:
        log.debug("chart series failed %s: %s", sym, exc)
    with _cache_lock:
        if len(_chart_cache) >= _CACHE_MAX_ENTRIES:
            _evict_expired(_chart_cache, _CHART_TTL_S)
        _chart_cache[key] = (time.time(), result)
    return result


# The fundamentals the chart can plot against price, grouped the way a reader
# thinks about them. `row` is yfinance's own label — kept as data rather than
# guessed at each call site, because these strings are the one part of this
# that upstream can rename without warning.
FUNDAMENTAL_METRICS: dict[str, dict] = {
    "revenue":      {"label": "Revenue",            "row": "Total Revenue",       "stmt": "income", "group": "Income statement"},
    "gross_profit": {"label": "Gross Profit",       "row": "Gross Profit",        "stmt": "income", "group": "Income statement"},
    "operating_income": {"label": "Operating Income", "row": "Operating Income",  "stmt": "income", "group": "Income statement"},
    "net_income":   {"label": "Net Income",         "row": "Net Income",          "stmt": "income", "group": "Income statement"},
    "diluted_eps":  {"label": "Diluted EPS",        "row": "Diluted EPS",         "stmt": "income", "group": "Income statement"},
    "ebitda":       {"label": "EBITDA",             "row": "EBITDA",              "stmt": "income", "group": "Income statement"},
    "ocf":          {"label": "Operating Cash Flow","row": "Operating Cash Flow", "stmt": "cash",   "group": "Cash flow"},
    "fcf":          {"label": "Free Cash Flow",     "row": "Free Cash Flow",      "stmt": "cash",   "group": "Cash flow"},
    "capex":        {"label": "Capital Expenditure","row": "Capital Expenditure", "stmt": "cash",   "group": "Cash flow"},
}

_fundamentals_cache: dict[str, tuple[float, dict]] = {}
_FUNDAMENTALS_TTL_S = 12 * 3600      # statements change quarterly; this is generous


def fundamentals_series(symbol: str, period: str = "quarterly") -> dict:
    """Financial-statement time series for plotting against price.

    Returns {metric_id: [{t, v}, ...]} plus the metric catalogue, oldest first.

    A metric upstream does not report is OMITTED rather than returned as an
    empty series — a menu entry that draws nothing is worse than one that is
    not offered, and coverage genuinely varies by company (an ETF has no
    revenue; not every filer reports EBITDA).
    """
    sym = symbol.upper()
    key = f"{sym}:{period}"
    hit = _fundamentals_cache.get(key)
    if hit and time.time() - hit[0] < _FUNDAMENTALS_TTL_S:
        return hit[1]

    out: dict[str, list[dict]] = {}
    try:
        import yfinance as yf
        t = yf.Ticker(sym)
        quarterly = period != "annual"
        income = t.quarterly_income_stmt if quarterly else t.income_stmt
        cash = t.quarterly_cashflow if quarterly else t.cashflow
        frames = {"income": income, "cash": cash}
        for mid, spec in FUNDAMENTAL_METRICS.items():
            df = frames.get(spec["stmt"])
            if df is None or getattr(df, "empty", True) or spec["row"] not in df.index:
                continue
            row = df.loc[spec["row"]]
            points = []
            # yfinance returns newest-first columns; a chart wants oldest-first.
            for col in reversed(list(df.columns)):
                v = row.get(col)
                if v is None or v != v:          # drop NaN
                    continue
                points.append({"t": str(col)[:10], "v": float(v)})
            if len(points) >= 2:                 # one point is not a series
                out[mid] = points
    except Exception as exc:
        log.debug("fundamentals series failed for %s: %s", sym, exc)

    result = {
        "symbol": sym,
        "period": "quarterly" if period != "annual" else "annual",
        "metrics": [
            {"id": mid, "label": spec["label"], "group": spec["group"],
             "unit": "eps" if mid == "diluted_eps" else "currency"}
            for mid, spec in FUNDAMENTAL_METRICS.items() if mid in out
        ],
        "series": out,
    }
    _fundamentals_cache[key] = (time.time(), result)
    return result


def macro_snapshot() -> dict:
    """Code-fetched macro facts — no interpretation, no LLM. Uses yfinance for
    ^TNX (10Y yield), ^VIX, plus a rate-proxy from the Fed funds rate. Cached
    for TTL seconds per run (macro data is slow-moving). Returns {} on failure."""
    global _macro_cache, _macro_cache_ts
    now = time.time()
    if _macro_cache is not None and (now - _macro_cache_ts) < _MACRO_CACHE_TTL_S:
        return _macro_cache
    with _macro_cache_lock:
        if _macro_cache is not None and (now - _macro_cache_ts) < _MACRO_CACHE_TTL_S:
            return _macro_cache
        try:
            import yfinance as yf
            tickers = yf.download(["^TNX", "^VIX", "^IRX"], period="30d", interval="1d",
                                  progress=False, auto_adjust=True, group_by="ticker")
            out: dict = {}
            for sym, label in [("^TNX", "us10y_pct"), ("^VIX", "vix"),
                               ("^IRX", "fed_funds_proxy_pct")]:
                try:
                    series = tickers[sym]["Close"] if isinstance(
                        tickers.columns, type(tickers.columns)) and sym in tickers.columns.get_level_values(0) \
                        else tickers["Close"]
                    series = series.dropna()
                    if len(series) >= 1:
                        out[label] = round(float(series.iloc[-1]), 2)
                    if len(series) >= 21:
                        out[f"{label}_1m_ago"] = round(float(series.iloc[-21]), 2)
                        out[f"{label}_1m_delta"] = round(float(series.iloc[-1] - series.iloc[-21]), 2)
                except Exception:
                    pass
            _macro_cache = out
            _macro_cache_ts = now
            return out
        except Exception as exc:
            log.debug("macro_snapshot failed: %s", exc)
            return {}


def sector_change_pct(sector: str | None) -> float | None:
    """Today's % change for the sector ETF matching the stock's sector. Cached 5 min."""
    global _sector_cache, _sector_cache_ts
    if not sector:
        return None
    etf = _SECTOR_MAP.get(sector)
    if not etf:
        return None
    now = time.time()
    if _sector_cache and now - _sector_cache_ts < _SECTOR_TTL_S:
        return _sector_cache.get(etf)
    try:
        import yfinance as yf
        dfs = yf.download(list(_SECTOR_MAP.values()), period="2d", interval="1d",
                          group_by="ticker", progress=False, threads=True,
                          auto_adjust=True)
        for sym in _SECTOR_MAP.values():
            if isinstance(dfs.columns, __import__('pandas').MultiIndex) and sym in dfs.columns.get_level_values(0):
                d = dfs[sym]
            else:
                continue
            closes = d["Close"].dropna()
            if len(closes) >= 2:
                _sector_cache[sym] = round(
                    (float(closes.iloc[-1]) - float(closes.iloc[-2])) / float(closes.iloc[-2]) * 100, 2)
        _sector_cache_ts = now
    except Exception as exc:
        log.debug("sector ETF download failed: %s", exc)
    return _sector_cache.get(etf)


_tape_cache: tuple[float, list[dict]] = (0.0, [])
_TAPE_TTL_S = 60


def market_tape() -> list[dict]:
    """The index / commodity / crypto strip across the top of the terminal.

    One batched yfinance download for every symbol in MARKET_TAPE, cached for
    a minute — this is glanceable context, not a trading feed, and refetching
    eight symbols on every page poll would be the most expensive thing the
    terminal does for the least benefit.

    Returns [{symbol, label, price, change_pct}]. A symbol that fails to
    resolve is simply omitted rather than rendered as a zero, because a tape
    showing 0.00 reads as a crashed market rather than a missing quote.
    """
    global _tape_cache
    ts, cached = _tape_cache
    if cached and time.time() - ts < _TAPE_TTL_S:
        return cached

    from alphadesk.config import MARKET_TAPE
    pairs = []
    for entry in MARKET_TAPE:
        sym, _, label = entry.partition(":")
        pairs.append((sym.strip(), (label or sym).strip()))
    if not pairs:
        return []

    out: list[dict] = []
    try:
        import yfinance as yf
        data = yf.download([s for s, _ in pairs], period="5d", interval="1d",
                           group_by="ticker", progress=False, threads=True,
                           auto_adjust=True)
        for sym, label in pairs:
            try:
                closes = data[sym]["Close"].dropna()
                if len(closes) < 2:
                    continue
                last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                if not prev:
                    continue
                out.append({"symbol": sym, "label": label,
                            "price": round(last, 2),
                            "change_pct": round(100.0 * (last - prev) / prev, 2)})
            except Exception:
                continue          # one bad symbol must not empty the tape
    except Exception as exc:
        log.debug("market tape failed: %s", exc)
        return cached             # keep the last good tape rather than blanking it

    if out:
        _tape_cache = (time.time(), out)
    return out


_index_cache: tuple[float, list[dict]] = (0.0, [])
_INDEX_TTL_S = 60


def index_board() -> list[dict]:
    """The cross-asset panel: indices, rates, commodities and FX.

    Same shape and same upstream as market_tape(), against the wider
    INDEX_BOARD list — see the config note for why the two lists are separate
    rather than one shared with the strip.
    """
    global _index_cache
    ts, cached = _index_cache
    if cached and time.time() - ts < _INDEX_TTL_S:
        return cached

    from alphadesk.config import INDEX_BOARD
    pairs = []
    for entry in INDEX_BOARD:
        sym, _, label = entry.partition(":")
        pairs.append((sym.strip(), (label or sym).strip()))
    if not pairs:
        return []

    out: list[dict] = []
    try:
        import yfinance as yf
        data = yf.download([s for s, _ in pairs], period="5d", interval="1d",
                           group_by="ticker", progress=False, threads=True,
                           auto_adjust=True)
        for sym, label in pairs:
            try:
                closes = data[sym]["Close"].dropna()
                if len(closes) < 2:
                    continue
                last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                if not prev:
                    continue
                out.append({"symbol": sym, "label": label,
                            "price": round(last, 4 if last < 10 else 2),
                            "change_pct": round(100.0 * (last - prev) / prev, 2)})
            except Exception:
                continue          # one bad symbol must not empty the board
    except Exception as exc:
        log.debug("index board failed: %s", exc)
        return cached

    if out:
        _index_cache = (time.time(), out)
    return out


_crypto_cache: tuple[float, dict] = (0.0, {})
_CRYPTO_TTL_S = 120
_CRYPTO_SPARK_POINTS = 24


def _rank_crypto(rows: list[dict], top: int) -> dict:
    """The four views, split out from the fetch so the ordering is testable
    without a network client.

    `dollar_volume` is a ranking input, not a column — it is dropped on the way
    out so nothing renders a turnover figure that is Alpaca-venue-only.
    """
    by_turnover = sorted(rows, key=lambda r: r["dollar_volume"], reverse=True)
    by_change = sorted(rows, key=lambda r: r["change_pct"], reverse=True)

    def strip(rs):
        return [{k: v for k, v in r.items() if k != "dollar_volume"} for r in rs[:top]]

    return {
        # "All" keeps the config's own order — the reader's list, unsorted.
        "all": strip(rows),
        "most_active": strip(by_turnover),
        "gainers": strip([r for r in by_change if r["change_pct"] > 0]),
        "losers": strip([r for r in reversed(by_change) if r["change_pct"] < 0]),
    }


def crypto_movers(top: int = 20) -> dict:
    """{all, most_active, gainers, losers} over CRYPTO_UNIVERSE.

    ALPACA, not yfinance. The first cut of this scraped Yahoo and returned 4 of
    18 rows under throttling while the same request to Alpaca returned every
    one — and a panel that silently sheds three quarters of its rows is worse
    than no panel, because nothing on screen says it is incomplete.

    WHAT "MOST ACTIVE" MEANS HERE. The turnover is Alpaca's own venue, not
    consolidated crypto volume — BTC prints ~11 coins a day on it. Ranking by
    it is honest as "busiest on this feed" and wrong as "busiest in crypto",
    the same distinction ingest/stream.py draws about IEX. Gainers and losers
    are price-derived and carry no such caveat.

    Change is measured over a ROLLING 24 HOURS, not against a previous close.
    Crypto has no close: a daily bar's boundary is a midnight the market never
    observed, so a session-change figure here would disagree with every venue
    the reader can check. Hourly bars give the 24h-ago price, the 24h turnover
    and the spark off one request; the snapshot supplies a live last price.
    """
    global _crypto_cache
    ts, cached = _crypto_cache
    if cached and time.time() - ts < _CRYPTO_TTL_S:
        return cached

    from alphadesk.config import CRYPTO_UNIVERSE
    pairs = []
    for entry in CRYPTO_UNIVERSE:
        sym, _, label = entry.partition(":")
        pairs.append((sym.strip(), (label or sym).strip()))
    if not pairs:
        return {}
    symbols = [s for s, _ in pairs]
    labels = dict(pairs)

    try:
        from datetime import datetime, timedelta, timezone
        from alpaca.data.historical import CryptoHistoricalDataClient
        from alpaca.data.requests import CryptoBarsRequest, CryptoSnapshotRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        client = _bound(CryptoHistoricalDataClient(os.environ.get("ALPACA_API_KEY"),
                                                   os.environ.get("ALPACA_SECRET_KEY")))
        end_t = datetime.now(timezone.utc)
        bars = client.get_crypto_bars(CryptoBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame(1, TimeFrameUnit.Hour),
            start=end_t - timedelta(days=2), end=end_t)).data
        try:
            snaps = client.get_crypto_snapshot(
                CryptoSnapshotRequest(symbol_or_symbols=symbols))
        except Exception:
            snaps = {}            # bars alone still answer every column
    except Exception as exc:
        log.debug("crypto movers failed: %s", exc)
        return cached

    rows: list[dict] = []
    for sym in symbols:
        series = list(bars.get(sym) or [])
        if len(series) < 2:
            continue              # omit rather than render an unpriced row
        closes = [float(b.close) for b in series]
        snap = snaps.get(sym) if snaps else None
        live = getattr(getattr(snap, "latest_trade", None), "price", None)
        last = float(live) if live else closes[-1]
        # 25 hourly bars back is 24 hours of elapsed time.
        prev = closes[-25] if len(closes) >= 25 else closes[0]
        if not prev or not last:
            continue
        window = series[-_CRYPTO_SPARK_POINTS:]
        vol = sum(float(getattr(b, "volume", 0) or 0) for b in window)
        dp = 6 if last < 1 else (4 if last < 100 else 2)
        rows.append({
            "symbol": sym,
            "name": labels.get(sym, sym),
            "price": round(last, dp),
            "change_pct": round(100.0 * (last - prev) / prev, 2),
            "volume": int(vol),
            "dollar_volume": vol * last,
            "spark": [round(float(b.close), dp) for b in window],
        })

    if not rows:
        return cached

    result = _rank_crypto(rows, top)
    _crypto_cache = (time.time(), result)
    return result


_quote_cache: dict[str, tuple[float, dict | None]] = {}
_QUOTE_TTL_S = 60


def quote(symbol: str) -> Optional[dict]:
    """The equity-overview readout: the numbers a quote page pins next to a
    price. One yfinance .info call, cached a minute.

    bid_size/ask_size are returned in SHARES. yfinance reports them in round
    lots, so they are multiplied by 100 — quoting "219.00 x 1" when the book
    shows 100 shares would be wrong by two orders of magnitude.
    """
    sym = symbol.upper()
    with _cache_lock:
        hit = _quote_cache.get(sym)
        if hit and time.time() - hit[0] < _QUOTE_TTL_S:
            return hit[1]

    out: dict | None = None
    try:
        import yfinance as yf
        info = yf.Ticker(sym).info or {}
        if not info.get("previousClose"):
            raise ValueError("no quote data")

        from datetime import datetime, timezone

        from alphadesk.config import ET

        def _epoch_date(v):
            """A calendar DATE, read in UTC.

            yfinance encodes these as UNIX seconds, but two different things
            arrive that way. An ex-dividend date is a date-stamp — midnight UTC
            standing for the day itself — so converting it to Eastern moves it
            back an evening and reports the wrong day: 2026-06-04 arrives as
            00:00Z and reads as June 3rd in New York. Earnings, by contrast, is
            a real instant (20:00Z, after the close) and survives either
            reading. UTC is the one that is right for both.
            """
            try:
                return datetime.fromtimestamp(float(v), timezone.utc).date().isoformat()
            except Exception:
                return None

        def _epoch_ts(v):
            """A moment, in Eastern — this one IS an instant, and the terminal
            states times in market time."""
            try:
                return datetime.fromtimestamp(float(v), timezone.utc).astimezone(ET).isoformat()
            except Exception:
                return None

        rt = _live_last_trade(sym)
        prev = float(info["previousClose"])
        last = rt[0] if rt else info.get("regularMarketPrice") or prev
        out = {
            "symbol": sym,
            "name": info.get("longName") or info.get("shortName") or sym,
            "exchange": info.get("exchange"),
            # The venue as a reader recognises it ("NasdaqGS"), plus how the
            # price is sourced. `exchange` alone is the terse code, NMS.
            "exchange_name": info.get("fullExchangeName") or info.get("exchange"),
            "quote_source": info.get("quoteSourceName"),
            # When this quote was struck. Without it a stale panel and a live
            # one look the same, which is the same complaint the stream's
            # `stale` flag answers for ticks.
            "as_of": _epoch_ts(info.get("regularMarketTime")),
            "currency": info.get("currency") or "USD",
            "price": round(float(last), 2),
            "change": round(float(last) - prev, 2),
            "change_pct": round(100.0 * (float(last) - prev) / prev, 2) if prev else None,
            "previous_close": prev,
            "open": info.get("open"),
            "bid": info.get("bid"),
            "ask": info.get("ask"),
            "bid_size": (info.get("bidSize") or 0) * 100,
            "ask_size": (info.get("askSize") or 0) * 100,
            "day_low": info.get("dayLow"),
            "day_high": info.get("dayHigh"),
            "week52_low": info.get("fiftyTwoWeekLow"),
            "week52_high": info.get("fiftyTwoWeekHigh"),
            "volume": info.get("volume"),
            "avg_volume": info.get("averageVolume"),
            "market_cap": info.get("marketCap"),
            "enterprise_value": info.get("enterpriseValue"),
            "pe_forward": info.get("forwardPE"),
            "pe_trailing": info.get("trailingPE"),
            "peg": info.get("pegRatio"),
            "price_to_sales": info.get("priceToSalesTrailing12Months"),
            "price_to_book": info.get("priceToBook"),
            "beta": info.get("beta"),
            "eps_ttm": info.get("trailingEps"),
            "dividend_yield": info.get("dividendYield"),
            # The annual rate beside the yield, which is how a dividend is
            # quoted — a percentage alone does not say what you receive.
            "dividend_rate": info.get("dividendRate"),
            "ex_dividend_date": _epoch_date(info.get("exDividendDate")),
            "earnings_date": _epoch_date(
                info.get("earningsTimestamp") or info.get("earningsTimestampStart")),
            "target_mean": info.get("targetMeanPrice"),
            "target_low": info.get("targetLowPrice"),
            "target_high": info.get("targetHighPrice"),
            "analyst_rating": (info.get("recommendationKey") or "").replace("_", " ") or None,
            "analyst_count": info.get("numberOfAnalystOpinions"),
        }
    except Exception as exc:
        log.debug("quote failed %s: %s", sym, exc)

    with _cache_lock:
        if len(_quote_cache) >= _CACHE_MAX_ENTRIES:
            _evict_expired(_quote_cache, _QUOTE_TTL_S)
        _quote_cache[sym] = (time.time(), out)
    return out


_movers_cache: tuple[float, dict] = (0.0, {})
_MOVERS_TTL_S = 120

# Raw screener output is dominated by sub-dollar warrants and rights: a $0.01
# ticker printing +900% is arithmetically a "gainer" and informationally
# nothing. These two filters are what make the list resemble a mover board
# rather than a list of broken instruments.
_MOVERS_MIN_PRICE = float(os.environ.get("MOVERS_MIN_PRICE", "5"))
# A price floor alone does not clean up gainers/losers: a $25 microcap can
# still print +500% on almost no turnover. Dollar volume is what separates "the
# market repriced this" from "someone bought a thousand shares of it".
# $1M measured against the real distribution: on a typical day the biggest
# percentage gainer turns over ~$20M and the tail falls off fast, so a $10M
# floor left a single name. Note that gainers/losers are inherently small-cap
# heavy — a percentage screen over the whole market always is. Large names
# live on the most-active tab, which ranks by volume instead.
_MOVERS_MIN_DOLLAR_VOL = float(os.environ.get("MOVERS_MIN_DOLLAR_VOL", "1000000"))


def _is_tradeable_symbol(sym: str) -> bool:
    """Exclude warrants, rights and units — they carry suffixes that make them
    look like enormous movers while being untradeable in any normal sense."""
    s = sym.upper()
    return not (s.endswith("W") and len(s) > 4) and not s.endswith(("WW", "R", "U"))


def _snapshot_prices(symbols: list[str]) -> dict[str, dict]:
    """Batch latest price + previous close for a list of symbols.

    Needed because Alpaca's most-actives response carries only symbol, volume
    and trade_count — no price at all. Without this the price filter below has
    nothing to filter ON, and the board fills with sub-dollar tickers.
    """
    if not symbols:
        return {}
    client = _alpaca_data_client()
    if client is None:
        return {}
    try:
        from alpaca.data.requests import StockSnapshotRequest
        snaps = client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=symbols))
    except Exception as exc:
        log.debug("snapshot batch failed: %s", exc)
        return {}
    out: dict[str, dict] = {}
    for sym, snap in (snaps or {}).items():
        try:
            trade = getattr(snap, "latest_trade", None)
            daily = getattr(snap, "previous_daily_bar", None)
            px = float(getattr(trade, "price", 0) or 0)
            prev = float(getattr(daily, "close", 0) or 0)
            if not px:
                continue
            today = getattr(snap, "daily_bar", None)
            vol = float(getattr(today, "volume", 0) or 0) or float(getattr(daily, "volume", 0) or 0)
            out[sym] = {
                "price": round(px, 2),
                "change_pct": round(100.0 * (px - prev) / prev, 2) if prev else None,
                "volume": int(vol),
                "dollar_volume": px * vol,
            }
        except Exception:
            continue
    return out


_SPARK_POINTS = 40


def _spark_series(symbols: list[str]) -> dict[str, list[float]]:
    """One batched bars call -> a short close series per symbol, for the
    sparkline on each movers row.

    Deliberately coarse — 15-minute bars over the last few sessions. A
    sparkline is 64 pixels wide, so minute resolution across ~60 symbols would
    be a far heavier request than the picture can show. Returns {} on any
    failure: a row without a spark renders without one, which is the same
    honesty rule the chart's indicator gate follows.
    """
    if not symbols:
        return {}
    client = _alpaca_data_client()
    if client is None:
        return {}
    try:
        from datetime import timedelta

        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        resp = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame(15, TimeFrameUnit.Minute),
            start=now_et() - timedelta(days=4),
            feed=DataFeed.IEX))
        data = resp.data if hasattr(resp, "data") else {}
    except Exception as exc:
        log.debug("spark batch failed: %s", exc)
        return {}
    out: dict[str, list[float]] = {}
    for sym, rows in (data or {}).items():
        closes = [float(b.close) for b in rows][-_SPARK_POINTS:]
        if len(closes) >= 2:            # one point draws nothing
            out[sym] = [round(c, 4) for c in closes]
    return out


def movers(top: int = 20) -> dict:
    """{most_active, gainers, losers}, each
    [{symbol, name, price, change_pct, volume, spark}].

    Alpaca's screener, then filtered and priced. Cached 2 minutes: this is a
    board you glance at, and the list barely moves minute to minute.
    """
    global _movers_cache
    ts, cached = _movers_cache
    if cached and time.time() - ts < _MOVERS_TTL_S:
        return cached

    try:
        from alpaca.data.historical.screener import ScreenerClient
        from alpaca.data.requests import MarketMoversRequest, MostActivesRequest
        client = _bound(ScreenerClient(os.environ["ALPACA_API_KEY"],
                                       os.environ["ALPACA_SECRET_KEY"]))
    except Exception as exc:
        log.debug("screener unavailable: %s", exc)
        return cached

    raw: dict[str, list] = {"most_active": [], "gainers": [], "losers": []}
    try:
        # Over-fetch: the instrument and price filters discard most of it, so
        # asking for exactly `top` would come back short.
        act = client.get_most_actives(MostActivesRequest(top=100))
        raw["most_active"] = list(getattr(act, "most_actives", []))
        # The movers endpoint caps `top` at 50 and 400s above it — actives
        # allows 100. Different limits on two calls in the same API.
        mv = client.get_market_movers(MarketMoversRequest(top=50))
        raw["gainers"] = list(getattr(mv, "gainers", []))
        raw["losers"] = list(getattr(mv, "losers", []))
    except Exception as exc:
        log.warning("movers fetch failed: %s", exc)
        return cached

    # One snapshot call covers every candidate across all three lists.
    candidates = sorted({getattr(r, "symbol", "") for rows in raw.values() for r in rows
                         if getattr(r, "symbol", "") and _is_tradeable_symbol(r.symbol)})
    priced = _snapshot_prices(candidates)

    def _build(rows) -> list[dict]:
        out = []
        for r in rows:
            sym = getattr(r, "symbol", "")
            if not sym or not _is_tradeable_symbol(sym):
                continue
            snap = priced.get(sym)
            # No price means no filter is possible, and an unpriced row would
            # render as a blank cell — drop it rather than show a hole.
            if not snap or not snap.get("price"):
                continue
            if snap["price"] < _MOVERS_MIN_PRICE:
                continue
            if snap.get("dollar_volume", 0) < _MOVERS_MIN_DOLLAR_VOL:
                continue
            out.append({
                "symbol": sym,
                "name": company_name(sym),
                "price": snap["price"],
                # Prefer the screener's own change when it supplies one (it is
                # the field the ranking was computed on); fall back to the
                # snapshot, which is all most-actives rows have.
                "change_pct": round(float(getattr(r, "percent_change", 0) or 0), 2)
                              or snap.get("change_pct"),
                # Screener rows for gainers/losers carry no volume at all —
                # fall back to the snapshot so every row can show turnover.
                "volume": int(getattr(r, "volume", 0) or 0) or snap.get("volume", 0),
            })
            if len(out) >= top:
                break
        return out

    result = {k: _build(v) for k, v in raw.items()}
    # Sparks are fetched AFTER filtering, for the union of rows that actually
    # render — the candidate pool going in is ~150 symbols and most are dropped
    # by the price and turnover floors.
    sparks = _spark_series(sorted({r["symbol"] for rows in result.values() for r in rows}))
    for rows in result.values():
        for r in rows:
            r["spark"] = sparks.get(r["symbol"], [])
    if any(result.values()):
        _movers_cache = (time.time(), result)
    return result
