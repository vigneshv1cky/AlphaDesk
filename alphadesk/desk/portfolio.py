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


def route_pick(pick_id: int, symbol: str, direction: str, price: float,
               conviction: float, session: str) -> bool:
    """Place the order on Alpaca paper and stamp broker_order_id/status/qty.

    $10 fractional notional per trade (TRADE_NOTIONAL_USD). OPEN → fractional
    market order; PRE/AFTER → extended-hours fractional limit at the decision price
    (only when PM_EXTENDED_HOURS=1; otherwise closed-market picks wait for the
    9:30 open under Model A). Returns True if routed; never raises — a failure is
    logged and the pick stays un-routed."""
    try:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        from alphadesk.config import PM_EXTENDED_HOURS, TRADE_NOTIONAL_USD
        from alphadesk.ledger import store

        if not price or price <= 0:
            return False
        side = OrderSide.BUY if direction == "LONG" else OrderSide.SELL
        client = _trading_client()
        if session == "OPEN":
            req = MarketOrderRequest(symbol=symbol, notional=round(TRADE_NOTIONAL_USD, 2),
                                     side=side, time_in_force=TimeInForce.DAY)
        elif PM_EXTENDED_HOURS:
            req = LimitOrderRequest(symbol=symbol, qty=_fractional_qty(price), side=side,
                                    limit_price=round(price, 2),
                                    time_in_force=TimeInForce.DAY,
                                    extended_hours=True)
        else:
            return False   # closed-market pick waits for the open (Model A)
        order = client.submit_order(req)
        order_id = str(getattr(order, "id", ""))
        store.set_broker_order(pick_id, order_id, getattr(order, "status", ""),
                               _fractional_qty(price))
        log.info("Routed #%d %s %s $%.2f (%.4f sh) → order %s", pick_id, symbol,
                 direction, TRADE_NOTIONAL_USD, _fractional_qty(price), order_id)
        return True
    except Exception as exc:
        log.warning("route_pick %d %s failed: %s", pick_id, symbol, exc)
        return False


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
