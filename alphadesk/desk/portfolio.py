"""Alpaca PAPER portfolio manager — OPT-IN (PAPER_TRADING=1).

Routes booked picks to an Alpaca PAPER account so the ledger reflects REAL fills
(slippage, borrow availability, order semantics) instead of Model-A assumptions.
The broker's actual fill becomes the ledger entry (grader + watcher prefer
broker_fill_price), so broker and ledger never disagree.

Idempotent and fail-closed: a pick is routed at most once; any API error leaves
it un-routed (Model A) rather than risking a phantom order. Off by default —
nothing trades until you opt in.
"""

import logging
import os
import threading

log = logging.getLogger("alphadesk.portfolio")

_client = None
_client_lock = threading.Lock()

# _position_watch_loop (60s) and _quantity_watch_loop (5s) in main.py both poll
# independently and can both decide to close the same pick within their
# respective windows. store.record_exit is idempotent at the ledger level, but
# _place_close (below) submits a broker order BEFORE that guard is ever
# checked — an unguarded race where both loops could each submit a closing
# order for the same broker position. This lock+set makes close_and_exit
# itself idempotent, not just the ledger write.
_closing_lock = threading.Lock()
_closing_ids: set[int] = set()


def _trading_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from alpaca.trading.client import TradingClient
                _client = TradingClient(
                    os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)
    return _client


def _fractional_qty(price: float) -> float:
    """$10 fractional sizing — every trade buys/sells exactly TRADE_NOTIONAL_USD of
    the name (fractional shares), so the paper fills match the $10/trade the
    dashboard already assumes."""
    from alphadesk.config import TRADE_NOTIONAL_USD
    return round(TRADE_NOTIONAL_USD / price, 4) if price > 0 else 0.0


def _short_qty(price: float) -> int:
    """Whole-share sizing for opening a SHORT. Alpaca doesn't support fractional
    or notional sizing for short sales (only buy-to-open / sell-to-close are
    notional-eligible) — a notional SELL-to-open is rejected by the API. This
    approximates the same ~TRADE_NOTIONAL_USD target, rounded to the nearest
    whole share (minimum 1), so short position size is less precise than long
    (inherent to the broker's constraint, not a choice)."""
    from alphadesk.config import TRADE_NOTIONAL_USD
    return max(1, round(TRADE_NOTIONAL_USD / price)) if price > 0 else 1


def route_pick(pick_id: int, symbol: str, direction: str, price: float,
               conviction: float, session: str) -> bool | None:
    """Place the order on Alpaca paper and stamp broker_order_id/status/qty.

    $10 fractional notional per trade (TRADE_NOTIONAL_USD) for LONG (fractional
    market order); whole-share qty for SHORT (see _short_qty — Alpaca doesn't
    support notional/fractional sizing for opening a short, a notional SELL is
    rejected by the API). Entries are OPEN-only (see desk/stream.py's session
    gate), so this only ever places a regular-hours market order — no
    extended-hours limit path. Returns True if routed, or False on an actual
    failure (bad price, API error — the caller should NOT take the pick).
    Never raises."""
    try:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        from alphadesk.config import TRADE_NOTIONAL_USD
        from alphadesk.ledger import store

        if not price or price <= 0:
            return False
        side = OrderSide.BUY if direction == "LONG" else OrderSide.SELL
        client = _trading_client()
        if direction == "LONG":
            req = MarketOrderRequest(symbol=symbol, notional=round(TRADE_NOTIONAL_USD, 2),
                                     side=side, time_in_force=TimeInForce.DAY)
            qty = _fractional_qty(price)
        else:
            qty = _short_qty(price)
            req = MarketOrderRequest(symbol=symbol, qty=qty, side=side,
                                     time_in_force=TimeInForce.DAY)
        order = client.submit_order(req)
        order_id = str(getattr(order, "id", ""))
        store.set_broker_order(pick_id, order_id, getattr(order, "status", ""), qty)
        log.info("Routed #%d %s %s $%.2f (%s sh) → order %s", pick_id, symbol,
                 direction, TRADE_NOTIONAL_USD, qty, order_id)
        return True
    except Exception as exc:
        log.warning("route_pick %d %s failed: %s", pick_id, symbol, exc)
        return False


def _place_close(pick: dict, price: float) -> float | None:
    """Place the order that CLOSES a routed position on the broker (SELL a LONG,
    buy-to-cover a SHORT), at the broker's actual available qty (NOT the
    broker_qty estimate stamped at entry — a notional buy fills at whatever
    fractional share count $ actually buys, which never exactly matches the
    pre-fill estimate, so selling the estimate gets rejected with "insufficient
    qty available" every time). Market in regular hours, extended-hours limit at
    the current price otherwise — UNLIKE route_pick (entries), closing is not
    gated on PM_EXTENDED_HOURS: entries can wait for the next open, but a
    position we already hold needs to close regardless of session, or it's
    orphaned at the broker while the caller (close_and_exit) marks the ledger
    exited anyway (2026-08-13 incident: this used to return None outside OPEN
    with extended hours off, and the ledger still recorded the exit). Polls
    briefly for a market fill; returns the actual fill price, or None if not
    filled in time (the ledger then uses the planned exit price and the DAY
    limit may still fill later). Best-effort."""
    import time

    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

    from alphadesk.config import session as market_session

    try:
        client = _trading_client()
        try:
            position = client.get_open_position(pick["symbol"])
            qty = float(getattr(position, "qty_available", None) or position.qty)
        except Exception:
            return None   # no open broker position left to close
        if qty <= 0:
            return None
        side = OrderSide.SELL if pick["direction"] == "LONG" else OrderSide.BUY
        if market_session() == "OPEN":
            req = MarketOrderRequest(symbol=pick["symbol"], qty=qty, side=side,
                                     time_in_force=TimeInForce.DAY)
        else:
            req = LimitOrderRequest(symbol=pick["symbol"], qty=qty, side=side,
                                    limit_price=round(price, 2),
                                    time_in_force=TimeInForce.DAY, extended_hours=True)
        order = client.submit_order(req)
        order_id = str(getattr(order, "id", ""))
        log.info("Close order %s for #%d %s qty=%s → %s", order_id, pick["id"],
                 pick["symbol"], qty, pick["direction"])
        for _ in range(10):   # ~5s to catch a market fill
            time.sleep(0.5)
            o = client.get_order_by_id(order_id)
            st = getattr(o, "status", "")
            if st == "filled":
                fap = getattr(o, "filled_avg_price", None)
                return float(fap) if fap else price
            if st in ("cancelled", "expired", "rejected"):
                return None
        return None
    except Exception as exc:
        log.warning("close_position #%d %s failed: %s", pick["id"], pick["symbol"], exc)
        return None


def close_and_exit(pick: dict, reason: str, exit_px: float, spy_now: float | None) -> bool:
    """CLOSE a routed position on the broker (real paper fill at the actual price),
    then record the exit in the ledger with that fill. For non-routed (Model-A)
    picks this is just a normal ledger exit. Idempotent (record_exit guards it,
    AND this function guards itself against the two position watchers racing to
    close the same pick concurrently — see _closing_ids)."""
    pick_id = pick["id"]
    with _closing_lock:
        if pick_id in _closing_ids:
            log.info("close_and_exit #%d %s — already closing, skipping", pick_id, pick["symbol"])
            return False
        _closing_ids.add(pick_id)
    try:
        from alphadesk.config import PAPER_TRADING
        from alphadesk.desk.plan import realized_exit
        from alphadesk.ledger import store

        if PAPER_TRADING and pick.get("broker_order_id"):
            filled = _place_close(pick, exit_px)
            exit_px = filled if filled else exit_px
        # broker_fill_price is the price actually paid — prefer it over entry_price
        # (a pre-fill decision-time quote that can differ sharply from the real fill
        # on a thin/low-liquidity name) so realized P&L reflects the real trade.
        entry = (pick.get("broker_fill_price") or pick.get("entry_price")
                 or pick.get("plan_entry"))
        perf = realized_exit(pick["direction"], entry, exit_px,
                             pick.get("spy_price"), spy_now,
                             bool(pick.get("low_liquidity")))
        return store.record_exit(pick_id, reason, **perf)
    finally:
        with _closing_lock:
            _closing_ids.discard(pick_id)


def reconcile_all() -> int:
    """Sync broker order status for routed-but-unfilled picks: stamp fills
    (broker_fill_price/ts become the ledger entry) and mark terminal non-fills
    (rejected/cancelled/expired) as not-taken. Returns rows updated."""
    from alphadesk.ledger import store

    picks = store.picks_with_open_broker_orders()
    if not picks:
        return 0
    updated = 0
    try:
        client = _trading_client()
        for p in picks:
            try:
                order = client.get_order_by_id(p["broker_order_id"])
                status = getattr(order, "status", "")
                if status in ("filled", "partially_filled"):
                    fap = getattr(order, "filled_avg_price", None)
                    fill_price = float(fap) if fap else None
                    fa = getattr(order, "filled_at", None)
                    fill_ts = fa.isoformat() if fa else None
                    if fill_price:
                        store.set_broker_fill(p["id"], fill_price, fill_ts or "")
                        store.update_pick(p["id"], broker_status="filled")
                        updated += 1
                elif status in ("cancelled", "expired", "rejected", "cancel_requested"):
                    store.record_exit(p["id"], f"not taken: broker order {status}")
                    store.update_pick(p["id"], broker_status=status)
                    updated += 1
                elif status and status != p.get("broker_status"):
                    store.update_pick(p["id"], broker_status=status)
            except Exception as exc:
                log.warning("reconcile pick %d failed: %s", p["id"], exc)
    except Exception as exc:
        log.warning("reconcile failed: %s", exc)
    return updated
