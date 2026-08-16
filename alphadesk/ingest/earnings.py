"""Earnings calendar — MARKET-WIDE: who reported (with the EPS surprise) and
who's about to, across the whole US tape, not a curated list.

Design law #1 (code owns facts, agents own judgment): this module supplies the
FACT "who reported / who's about to", filtered only by tradability (a factual
screen), and hands every reporter to the scout. The scout — not a hardcoded
watchlist — decides which are worth the team's attention. That removes the old
large-cap selection bias, so post-earnings drift can reach small/mid caps where
the edge actually lives; liquidity stays as EVIDENCE downstream (the grader's
double-friction haircut), never a gate here.

Source: the Nasdaq earnings calendar (api.nasdaq.com) — one call per date, no
API key, giving EPS estimate / actual / surprise% and the BMO/AMC session. It's
an undocumented endpoint, so every fetch is wrapped defensively: a bad day just
yields nothing and the next refresh heals it.

Two consumers:
  • upcoming_earnings()  → "be ready": what reports in the next N days
  • drift_candidates()   → post-earnings-drift candidates (reported, surprise known)
"""

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from alphadesk.config import (
    ET,
    EARNINGS_POST_MAX_DAYS,
    EARNINGS_PRE_WINDOW_DAYS,
    in_universe,
    now_et,
)
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.earnings")

_CAL_URL = "https://api.nasdaq.com/api/calendar/earnings?date={date}"
# Nasdaq blocks non-browser agents; these headers are required for a 200.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _f(v) -> float | None:
    """Parse a Nasdaq numeric string ('$1.23', '(0.45)', '89.08', 'N/A') → float|None."""
    if v is None:
        return None
    s = str(v).strip().replace("$", "").replace(",", "").replace("%", "")
    if not s or s.upper() in ("N/A", "NA", "--"):
        return None
    neg = s.startswith("(") and s.endswith(")")   # accounting negatives: (0.45)
    if neg:
        s = s[1:-1]
    try:
        f = float(s)
        f = -f if neg else f
        return f if f == f else None               # drop NaN
    except (TypeError, ValueError):
        return None


def _time_bucket(t: str | None) -> str:
    """Map Nasdaq's 'time' field → our session code."""
    t = (t or "").lower()
    if "pre-market" in t:
        return "BMO"           # before market open
    if "after-hours" in t:
        return "AMC"           # after market close
    return "DAY"               # time-not-supplied / intraday


def run_at(report_iso: str, session: str | None) -> str | None:
    """When to run Find Trades to catch the drift: 9:30 ET on the first trading
    session AFTER the result is public. BMO reports are out before that day's open
    (trade the same day); AMC / intraday reports first trade the next session."""
    try:
        dt = datetime.fromisoformat(report_iso).astimezone(ET)
    except (ValueError, TypeError):
        return None
    run_day = dt.date() if session == "BMO" else dt.date() + timedelta(days=1)
    while run_day.weekday() >= 5:      # skip Sat/Sun to the next weekday open
        run_day += timedelta(days=1)
    return datetime(run_day.year, run_day.month, run_day.day, 9, 30, tzinfo=ET).isoformat()


def reported_public(report_iso: str) -> datetime | None:
    """The moment a report counts as PUBLIC — the boundary between 'reporting
    soon' and 'just reported'. Nasdaq's BMO/AMC session tag is unreliable for
    the large majority of reporters (confirmed empirically: ~95% come back
    unclassified even close to the report date — a real data-coverage gap,
    not a parsing bug), so this no longer tries to pinpoint intraday timing
    at all. A report counts as public from midnight ET of its calendar date
    onward — just the date, no BMO/AMC/session distinction."""
    try:
        d = datetime.fromisoformat(report_iso[:10])   # date-only key
    except (ValueError, TypeError):
        return None
    return datetime(d.year, d.month, d.day, 0, 0, tzinfo=ET)


def _fetch_calendar_date(date_str: str) -> list[dict]:
    """One day of the Nasdaq earnings calendar → raw row dicts (empty on any error)."""
    req = urllib.request.Request(_CAL_URL.format(date=date_str), headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", "ignore"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        log.warning("earnings calendar fetch failed for %s: %s", date_str, exc)
        return []
    data = payload.get("data") or {}
    return data.get("rows") or []


def refresh_calendar(days_back: int = 5, days_fwd: int = 14) -> int:
    """Pull the market-wide earnings calendar for [today-days_back, today+days_fwd]
    into the ledger, keeping only Alpaca-tradable names. Returns rows upserted.

    days_fwd reaches ~2 weeks so the mega-caps (which cluster late in a reporting
    season) show up in the reporting-soon view, not just the nearest small-caps.

    report_date is stored DATE-ONLY so an event is keyed stably whether we see it
    pre-report (forecast row) or post-report (actual row) — the ON CONFLICT REPLACE
    then just fills in eps_actual/surprise as they land, never duplicating the event.
    """
    today = now_et().date()
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for offset in range(-days_back, days_fwd + 1):
        day = today + timedelta(days=offset)
        if day.weekday() >= 5:                       # markets closed — skip
            continue
        date_str = day.isoformat()
        for r in _fetch_calendar_date(date_str):
            sym = (r.get("symbol") or "").strip().upper()
            if not sym or not in_universe(sym):      # factual tradability screen
                continue
            key = (sym, date_str)
            if key in seen:
                continue
            seen.add(key)
            est = _f(r.get("epsForecast"))
            act = _f(r.get("eps"))                     # present only once reported
            surp = _f(r.get("surprise"))
            # Nasdaq sometimes omits surprise% even with both numbers — arithmetic
            # is ours to own, so compute it rather than mislabel a beat/miss as in-line.
            if surp is None and act is not None and est is not None and est != 0:
                surp = round((act - est) / abs(est) * 100, 2)
            rows.append({
                "symbol": sym,
                "report_date": date_str,             # date-only, stable key
                "session": _time_bucket(r.get("time")),
                "eps_estimate": est,
                "eps_actual": act,
                "surprise_pct": surp,
                "market_cap": _f(r.get("marketCap")),
            })
        time.sleep(0.4)                               # be polite to the endpoint
    store.upsert_earnings(rows)
    purged = store.purge_legacy_earnings()            # drop old full-timestamp dupes
    log.info("earnings calendar refreshed: %d tradable reporters across %d days"
             "%s", len(rows), days_back + days_fwd + 1,
             f" (purged {purged} legacy rows)" if purged else "")
    return len(rows)


def arm_upcoming_reports(days_ahead: int = 2) -> int:
    """PRE-ARM today's+upcoming reporters: warm each symbol's price/options caches
    and store its pre-report close + options-implied move in the calendar. The
    moment a report drops, the drift reaction is measured instantly against the
    exact pre-report baseline and the underreaction gauge already has the implied
    move — no cold fetch at release time. Best-effort; runs off the earnings loop."""
    from alphadesk.ingest import prices
    up = store.upcoming_earnings(days_ahead)
    n = 0
    for e in up:
        try:
            sym = e["symbol"]
            ctx = prices.get_context(sym)
            opt = prices.get_options_context(sym)
            pre_close = ctx.get("last_price") if ctx else None
            implied = None
            if opt:
                implied = opt.get("expected_move_1d_pct") or opt.get("expected_move_to_expiry_pct")
            store.update_earnings_arm(sym, e["report_date"], pre_close, implied)
            n += 1
        except Exception:
            continue
    if n:
        log.info("Armed %d upcoming reporters (pre-report close + implied move)", n)
    return n


def arm_liquidity(days_back: int = 4, days_fwd: int = 14) -> int:
    """Batch-compute and persist the same 20d-avg-$vol liquidity bar the trading
    pipeline gates entries on, for every symbol currently in the Earnings page's
    window. Runs off the earnings loop — a live per-request fetch for several
    hundred names took over two minutes and made the page itself unusable; this
    keeps that cost entirely off the request path. Best-effort."""
    from alphadesk.ingest import prices
    rows = store.earnings_window(days_back=days_back, days_fwd=days_fwd)
    syms = sorted({r["symbol"] for r in rows})
    if not syms:
        return 0
    liq = prices.liquidity_batch(syms)
    n = store.update_earnings_liquidity(liq) if liq else 0
    if n:
        log.info("Armed liquidity for %d/%d earnings-window symbols", len(liq), len(syms))
    return n


def drift_candidates() -> dict[str, list[dict]]:
    """Earnings-adjacent names → synthetic candidate articles, keyed by symbol.
    A CANDIDATE SOURCE: it lets the earnings calendar flow through the same
    candidate-pool shape the entry watcher consumes. The calendar fetch
    already ran (refresh_calendar, on the 6h loop); this just reads the rows
    the run needs and shapes them as candidates.

    One continuous window, unfiltered by reaction/momentum (facts only —
    a human reading the terminal is where the judgment lives):
    from EARNINGS_PRE_WINDOW_DAYS before the report through
    EARNINGS_POST_MAX_DAYS after it (-3 to +5 days around the report date,
    by default) — sourced as two pools (upcoming vs. already-reported are
    different queries) but no gap between them.
    """
    now = now_et()
    out: dict[str, list[dict]] = {}

    # ── Pre-earnings: reports within the next EARNINGS_PRE_WINDOW_DAYS ──────
    pre_candidates = store.upcoming_earnings(days=EARNINGS_PRE_WINDOW_DAYS)
    for p in pre_candidates:
        esym = p["symbol"]
        out[esym] = [{
            "id": f"pre-earnings-{esym}-{p['report_date']}",
            "title": f"[PRE-EARNINGS] {esym} reports {p['report_date']} {p.get('session', '')}",
            "summary": f"{esym} reports {p['report_date']} — watching for a technical setup ahead of the print.",
            "source": "PreEarnings", "url": "",
            "published_at": p["report_date"],
            "category": "PRE_EARNINGS", "tickers": [esym],
            "low_liquidity": bool(p.get("low_liquidity")),   # pre-armed by arm_liquidity()
            "mentions": [{"symbol": esym, "sentiment": 0.0, "label": "neutral",
                          "category": "PRE_EARNINGS"}],
            "relations": [],
        }]

    # ── Post-earnings: up to EARNINGS_POST_MAX_DAYS ago, already PUBLIC (past
    # their BMO/DAY 9:30 or AMC 16:00 boundary) — no lower bound, so this
    # picks up right where the pre-earnings window leaves off (age 0) ──
    reporters = [e for e in store.recently_reported(EARNINGS_POST_MAX_DAYS)
                 if (rp := reported_public(e["report_date"])) and rp <= now]
    for e in reporters:
        esym = e["symbol"]
        report_day = datetime.strptime(e["report_date"][:10], "%Y-%m-%d").date()
        age_days = (now.date() - report_day).days
        if age_days > EARNINGS_POST_MAX_DAYS:
            continue
        surp = e.get("surprise_pct")
        if surp is not None:
            verdict = "beat" if surp > 0 else ("miss" if surp < 0 else "in-line")
            eps_txt = f"EPS {e.get('eps_actual')} vs est {e.get('eps_estimate')} — {verdict} {surp}%"
        else:
            eps_txt = f"EPS est {e.get('eps_estimate')}"
        out[esym] = [{
            "id": f"earnings-{esym}-{e['report_date'][:10]}",
            "title": f"[EARNINGS] {esym} reported {e['report_date'][:10]} {e.get('session') or ''} ({age_days}d ago): {eps_txt}",
            "summary": f"{esym} reported {age_days} days ago — watching for a settled technical setup.",
            "source": "EarningsCalendar", "url": "", "published_at": e["report_date"],
            "category": "EARNINGS", "tickers": [esym],
            "low_liquidity": bool(e.get("low_liquidity")),   # pre-armed by arm_liquidity()
            "mentions": [{"symbol": esym, "sentiment": 0.0, "label": "neutral",
                          "category": "EARNINGS"}],
            "relations": [],
        }]
    return out
