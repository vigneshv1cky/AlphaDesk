"""Paper portfolio manager — routes the desk's booked picks to an Alpaca PAPER account and
reconciles them, so the Alpaca account is an honest real-fill scoreboard (real fills, slippage,
portfolio P&L) instead of only the internal simulated ledger.

OPT-IN: nothing routes to Alpaca unless `PAPER_TRADING` is set. Research/paper only — the Alpaca
PAPER endpoint (paper-api.alpaca.markets); no real money.

Design — a RECONCILIATION loop, not inline order-placing, so it is idempotent and restart-safe:
`reconcile()` makes Alpaca *match* the ledger's open-taken positions —
  • OPEN what the ledger holds but Alpaca doesn't (highest conviction first, capped at
    PM_MAX_POSITIONS; conviction-weighted size), stamping the order id on the pick;
  • CLOSE what Alpaca holds but the ledger has exited/graded.
Sizing is conviction-weighted: $PM_BASE_USD for a conviction-50 pick, scaled by adjusted_score,
capped at PM_MAX_POSITION_USD. So thin leans get tiny positions and high-conviction gets more —
selection re-expressed as SIZE now that the desk takes everything.

Limitations (v1): one position per SYMBOL (Alpaca aggregates); a short that isn't shortable is
rejected and not retried; partial fills aren't tracked beyond the submit. Reconcile only closes
positions it opened (stamped with a broker_order_id) — manual trades in the same paper account
are left alone.
"""

import logging
import threading

from alphadesk.config import (
    PAPER_TRADING,
    PM_BASE_USD,
    PM_EXTENDED_HOURS,
    PM_MAX_POSITION_USD,
    PM_MAX_POSITIONS,
)
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.portfolio")

_client = None
_client_lock = threading.Lock()


def _trading_client():
    """Lazily-built Alpaca PAPER trading client (same keys as the data client). None if the
    keys are missing or the SDK can't initialise."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                try:
                    import os

                    from alpaca.trading.client import TradingClient
                    _client = TradingClient(
                        os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"],
                        paper=True)
                except Exception as exc:      # missing keys / import failure
                    log.warning("Alpaca trading client unavailable: %s", exc)
                    return None
    return _client


def _size_shares(pick: dict, price: float | None) -> int:
    """Conviction-weighted whole-share size: $PM_BASE_USD at conviction 50, scaled linearly by
    adjusted_score (floor 0.1x), capped at PM_MAX_POSITION_USD. 0 if no usable price."""
    if not price or price <= 0:
        return 0
    conv = pick.get("adjusted_score") or pick.get("confidence") or 50
    dollars = min(PM_MAX_POSITION_USD, PM_BASE_USD * max(0.1, float(conv) / 50.0))
    return int(dollars // price)


def reconcile() -> dict:
    """Make the Alpaca paper account match the ledger's open-taken positions. Idempotent —
    safe to call on a loop. Returns a summary dict.

    Order construction by decision session (PM_EXTENDED_HOURS=1):
      • OPEN     → market order (fills now)
      • PRE/AFTER → LIMIT order at the decision price, extended_hours=True (weekday
        extended sessions only — there is no night session on Alpaca)
      • CLOSED   → nothing to route until the ledger has a fill (Model A: next open)
    A fill-sync pass stamps the broker's actual fill (broker_fill_price/ts) — the
    ledger's honest entry — and clears dead orders (expired/canceled) for re-route."""
    if not PAPER_TRADING:
        return {"enabled": False}
    client = _trading_client()
    if client is None:
        return {"enabled": True, "error": "no trading client"}
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

    try:
        positions = {p.symbol.upper(): p for p in client.get_all_positions()}
    except Exception as exc:
        log.warning("get_all_positions failed: %s", exc)
        return {"enabled": True, "error": str(exc)}

    open_taken = store.open_taken_picks()
    open_syms = {p["symbol"].upper() for p in open_taken}
    opened = closed = 0

    # FILL SYNC — resolve routed orders: stamp the broker's actual fill (the ledger's
    # honest entry), clear dead orders so the entry pass can re-route them.
    # Extended-hours limit orders that expired may have done so because the price gapped
    # far from the limit — if the gap exceeds ENTRY_GAP_SKIP_PCT the thesis rested on
    # a stale price and the pick is NOT-TAKEN rather than re-routed into a worse fill.
    from alphadesk.config import ENTRY_GAP_SKIP_PCT
    from alphadesk.ingest.prices import latest_prices as pm_latest_prices

    ext_syms_needing_price: set[str] = set()
    for pick in open_taken:
        oid = pick.get("broker_order_id")
        if not oid:
            continue
        try:
            o = client.get_order_by_id(oid)
        except Exception:
            continue   # unknown/transient — retry next pass
        state = str(getattr(o, "status", "") or "").lower()
        if state == "filled":
            px = float(getattr(o, "filled_avg_price", 0) or 0)
            ts = str(getattr(o, "filled_at", "") or "")
            if px and not pick.get("broker_fill_price"):
                store.set_broker_fill(pick["id"], px, ts)
            store.set_broker_order(pick["id"], oid, "filled", pick.get("broker_qty") or 0)
            log.info("PM fill #%d %s @ %s", pick["id"], pick["symbol"], px or "?")
        elif state in ("expired", "canceled", "rejected"):
            limit_px = pick.get("plan_entry")
            if (limit_px and ENTRY_GAP_SKIP_PCT > 0
                    and pick.get("session") in ("PRE", "AFTER")):
                ext_syms_needing_price.add(pick["symbol"].upper())
                pick["_gap_limit_px"] = limit_px   # stash for the gap check below
            else:
                store.set_broker_order(pick["id"], None, f"unfilled: {state}", 0)
                log.info("PM order %s for #%d %s — cleared for re-route", state,
                         pick["id"], pick["symbol"])

    # Gap-skip check for expired extended-hours limits: if the current price has
    # diverged >ENTRY_GAP_SKIP_PCT from the limit, the thesis rested on a stale
    # price → mark NOT-TAKEN (don't re-route into a worse fill).
    if ext_syms_needing_price:
        cur_prices = pm_latest_prices(list(ext_syms_needing_price))
        for pick in open_taken:
            limit_px = pick.pop("_gap_limit_px", None)
            if limit_px is None:
                continue
            cur = cur_prices.get(pick["symbol"].upper())
            if cur and abs(cur - limit_px) / limit_px > ENTRY_GAP_SKIP_PCT / 100.0:
                reason = (f"not taken: ext-hours limit {limit_px} expired — price gapped "
                          f"to {cur} ({abs(cur - limit_px) / limit_px * 100:.1f}% from "
                          f"decision price, >{ENTRY_GAP_SKIP_PCT}% threshold)")
                store.set_broker_order(pick["id"], None, reason, 0)
                store.record_exit(pick["id"], reason)
                log.info("PM gap-skip #%d %s: limit %s expired, price now %s — not taken",
                         pick["id"], pick["symbol"], limit_px, cur)
            else:
                store.set_broker_order(pick["id"], None,
                                       f"unfilled: {pick.get('broker_status', 'expired')}", 0)
                log.info("PM order unfilled for #%d %s — cleared for re-route (gap within threshold)",
                         pick["id"], pick["symbol"])

    # ENTRIES — open what the ledger has but Alpaca doesn't, best conviction first, capped.
    slots = len(positions)
    submitted: set[str] = set()
    for pick in sorted(open_taken, key=lambda p: -(p.get("adjusted_score") or 0)):
        sym = pick["symbol"].upper()
        status = pick.get("broker_status") or ""
        if (sym in positions or sym in submitted or pick.get("broker_order_id")
                or status.startswith("rejected") or status.startswith("filled")):
            continue   # already routed / held / dead
        price = pick.get("entry_price")
        # Extended-hours pick with no fill yet: route a LIMIT order at the decision
        # price (PRE/AFTER weekday sessions; PM_EXTENDED_HOURS). Else wait for the
        # Model-A open fill.
        limit_px = None
        if price is None and PM_EXTENDED_HOURS and pick.get("session") in ("PRE", "AFTER"):
            limit_px = pick.get("plan_entry")
        if price is None and limit_px is None:
            continue
        qty = _size_shares(pick, price or limit_px)
        if qty < 1:
            continue
        side = OrderSide.BUY if pick["direction"] == "LONG" else OrderSide.SELL
        try:
            if limit_px:
                order = client.submit_order(LimitOrderRequest(
                    symbol=sym, qty=qty, side=side, time_in_force=TimeInForce.DAY,
                    limit_price=round(float(limit_px), 2), extended_hours=True))
                store.set_broker_order(pick["id"], str(order.id), "submitted-ext", qty)
                log.info("PM extended-hours LIMIT %s %s x%d @ %s", pick["direction"], sym, qty, limit_px)
            else:
                order = client.submit_order(MarketOrderRequest(
                    symbol=sym, qty=qty, side=side, time_in_force=TimeInForce.DAY))
                store.set_broker_order(pick["id"], str(order.id), "submitted", qty)
                log.info("PM opened %s %s x%d (conv %s)", pick["direction"], sym, qty,
                         pick.get("adjusted_score"))
            submitted.add(sym)
            slots += 1
            opened += 1
        except Exception as exc:
            store.set_broker_order(pick["id"], None, f"rejected: {exc}", 0)
            log.warning("PM order REJECTED %s %s: %s", pick["direction"], sym, exc)
        if slots >= PM_MAX_POSITIONS:
            break

    # EXITS — close what Alpaca holds but the ledger has exited/graded (no longer
    # open-taken). ONLY positions the PM itself opened (broker_order_id stamped): an
    # unrecognized position (e.g. a manual trade in the same paper account) is left
    # alone, never liquidated.
    managed = store.pm_managed_symbols()
    for sym in positions:
        if sym not in open_syms and sym in managed:
            try:
                client.close_position(sym)
                closed += 1
                log.info("PM closed %s (ledger exited)", sym)
            except Exception as exc:
                log.warning("PM close failed %s: %s", sym, exc)

    return {"enabled": True, "opened": opened, "closed": closed,
            "alpaca_positions": len(positions)}
