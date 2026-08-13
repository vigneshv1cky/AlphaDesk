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

from alphadesk.config import ET, MATERIAL_REACTION_PCT, in_universe, now_et
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


def reported_public(report_iso: str, session: str | None) -> datetime | None:
    """The ET moment a report becomes PUBLIC — the boundary between 'reporting
    soon' and 'just reported'. BMO/DAY names are public at 4:00 ET (pre-market
    start, when extended-hours trading begins); AMC names after the 16:00 close.
    So a BMO name reporting today flips to 'just reported' at 4:00 today — tradeable
    in the pre-market session."""
    try:
        d = datetime.fromisoformat(report_iso[:10])   # date-only key
    except (ValueError, TypeError):
        return None
    hour, minute = (16, 0) if session == "AMC" else (4, 0)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET)


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


def drift_candidates(days: int) -> dict[str, list[dict]]:
    """Recently-reported names → synthetic [EARNINGS] candidate articles, keyed by
    symbol. A CANDIDATE SOURCE, parallel to news.poll: it lets post-earnings drift
    flow through the SAME scout → team pipeline as news. The calendar fetch already
    ran (refresh_calendar, on the 6h loop); this just reads the rows the run needs
    and shapes them as candidates — the caller merges them into the pool.
    """
    # Only reports already PUBLIC (past their BMO/DAY 9:30 or AMC 16:00 boundary) —
    # a not-yet-public reporter has no tradeable drift yet. Time-aware, NOT gated on
    # Nasdaq's lagged eps_actual (which made every same-day reporter invisible all
    # day — the OTLY +30% miss). The freshest names now reach the scout the moment
    # they can be traded, regardless of when the surprise number lands.
    now = now_et()
    reporters = [e for e in store.recently_reported(days)
                 if (rp := reported_public(e["report_date"], e.get("session"))) and rp <= now]
    # How much each name has ALREADY moved since its report went public — the realized
    # reaction (total, extended-hours aware) split into the uncapturable gap and the
    # capturable drift. total IS the direction signal: the drift edge bets the observed
    # REACTION, not the result (a beat that sells off is not a long). Best-effort.
    from alphadesk.config import session as market_session
    from alphadesk.ingest import prices
    moved = prices.moves_since_report(
        [{"symbol": e["symbol"], "report_date": e["report_date"],
          "session": e.get("session")} for e in reporters])
    # The shadow A/B enters like a real pick: if the market is OPEN at sighting, the
    # fill is the LIVE price now; else the next 9:30 open (resolved by the grader).
    # (Recording the market session + live price here is what makes the A/B's entry
    # clock identical to a booked pick's — see grade_reactions.)
    mkt = market_session()
    live_now = (prices.latest_prices([e["symbol"] for e in reporters])
                if mkt == "OPEN" and reporters else {})
    out: dict[str, list[dict]] = {}

    # ── Pre-earnings drift candidates: stocks reporting soon with visible momentum ──
    pre_candidates = store.upcoming_earnings(days=3)
    if pre_candidates:
        pre_syms = [p["symbol"] for p in pre_candidates]
        pre_px = prices.latest_prices(pre_syms) if mkt == "OPEN" and pre_syms else {}
        for p in pre_candidates:
            esym = p["symbol"]
            ctx = prices.get_context(esym)
            if not ctx:
                continue
            chg = ctx.get("change_5d_pct") or 0.0
            rvol = ctx.get("rvol") or 0.0
            if abs(chg) < 2.0 or rvol < 1.2:
                continue
            pre_dir = "LONG" if chg > 0 else "SHORT"
            sent = round(max(-1.0, min(1.0, chg / 5.0)), 3)
            out[esym] = [{
                "id": f"pre-earnings-{esym}-{p['report_date']}",
                "title": (f"[PRE-EARNINGS] {esym} reports {p['report_date']} {p.get('session', '')}: "
                          f"5d move {chg:+.1f}%, rvol {rvol:.1f}x — positioning ahead of print"),
                "summary": (f"Pre-earnings momentum: {esym} moving {chg:+.1f}% over 5d on "
                            f"{rvol:.1f}x volume ahead of {p['report_date']} report. "
                            f"Direction: {pre_dir}."),
                "source": "PreEarnings", "url": "",
                "published_at": p["report_date"],
                "category": "PRE_EARNINGS", "tickers": [esym],
                "reaction_pct": round(chg, 2),
                "low_liquidity": bool(p.get("low_liquidity")),   # pre-armed by arm_liquidity()
                "mentions": [{"symbol": esym, "sentiment": sent,
                              "label": "positive" if sent > 0 else "negative",
                              "category": "PRE_EARNINGS"}],
                "relations": [],
            }]
    # ── End pre-earnings ──

    for e in reporters:
        esym = e["symbol"]
        surp = e.get("surprise_pct")
        mv = moved.get(esym)   # {"total","gap","drift"} or None
        total = mv["total"] if mv else None   # full reaction so far (extended-hours aware)
        # Shadow A/B: log EVERY measurable reporter (gate-passed AND gate-dropped) so the
        # grader can forward-score both arms and reveal whether the gate cuts winners.
        # First sighting wins (ON CONFLICT IGNORE); no LLM cost. Recorded BEFORE the gate.
        if total is not None:
            from alphadesk.config import REACTION_AB_HORIZON_DAYS
            store.record_reaction({
                "symbol": esym, "report_date": e["report_date"][:10],
                "session": e.get("session"),
                "mkt_session": mkt,
                "direction": "LONG" if total >= 0 else "SHORT",
                "horizon_days": REACTION_AB_HORIZON_DAYS,
                "reaction_total": round(total, 3),
                "reaction_drift": round(mv["drift"], 3) if (mv and mv.get("drift") is not None) else None,
                "gate_passed": int(abs(total) >= MATERIAL_REACTION_PCT),
                "entry_price": live_now.get(esym.upper()),
            })
        # GATE: the drift edge rides a VISIBLE reaction. No material move since the
        # report = no reaction to continue — a pre-print / no-reaction earnings binary is
        # a coin flip, not drift, so don't emit a directional candidate. total is
        # extended-hours aware, so a pre-market reaction still counts as visible.
        if total is None or abs(total) < MATERIAL_REACTION_PCT:
            continue
        gap, drift = (mv["gap"], mv["drift"]) if mv else (None, None)   # may be None pre-session
        if surp is not None:
            verdict = "beat" if surp > 0 else ("miss" if surp < 0 else "in-line")
            eps_txt = f"EPS {e.get('eps_actual')} vs est {e.get('eps_estimate')} — {verdict} {surp}%"
        else:
            verdict = "reaction pending"
            eps_txt = f"EPS est {e.get('eps_estimate')} — actual not yet released (drift from reaction)"
        # The observed REACTION (total) is the direction; the capturable drift from the
        # open and the uncapturable gap are shown as context. Pre-regular-session the
        # reaction is entirely extended-hours (no gap/drift split yet).
        if drift is not None and gap is not None:
            mv_txt = (f"; {total:+.1f}% reaction — {drift:+.1f}% drift from open "
                      f"({gap:+.1f}% gap excluded)")
            mv_note = (f" Since the report: {total:+.1f}% total reaction — {gap:+.1f}% gap "
                       f"(uncapturable) then {drift:+.1f}% drift from the open (the tradeable leg).")
        else:
            mv_txt = f"; {total:+.1f}% reaction so far (extended-hours, no regular session yet)"
            mv_note = (f" Since the report: {total:+.1f}% reaction, still in extended hours — no "
                       "regular session has traded yet, so the reaction itself is the signal.")
        # Direction/sentiment from the observed reaction (total), not the raw surprise sign.
        sent = round(max(-1.0, min(1.0, total / 5.0)), 3)
        out[esym] = [{
            "id": f"earnings-{esym}-{e['report_date'][:10]}",
            "title": f"[EARNINGS] {esym} reported {e['report_date'][:10]} {e.get('session') or ''}: "
                     f"{eps_txt}{mv_txt}",
            "summary": f"Post-earnings-drift setup: {esym} — {verdict}.{mv_note}",
            "source": "EarningsCalendar", "url": "", "published_at": e["report_date"],
            "category": "EARNINGS", "tickers": [esym],
            "reaction_pct": round(total, 2),   # the raw reaction size — the scout-window rank signal
            "implied_move_pct": e.get("implied_move_pct"),   # pre-armed, exact baseline (1d options move)
            "pre_report_close": e.get("pre_report_close"),   # pre-armed close the reaction is measured from
            "low_liquidity": bool(e.get("low_liquidity")),   # pre-armed by arm_liquidity()
            "mentions": [{"symbol": esym, "sentiment": sent,
                          "label": ("positive" if sent > 0 else "negative" if sent < 0 else "neutral"),
                          "category": "EARNINGS"}],
            "relations": [],
        }]
    return out
