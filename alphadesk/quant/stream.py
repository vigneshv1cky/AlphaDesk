"""Real-time price streaming via Alpaca WebSocket. Pure code, zero LLM.

Maintains an in-memory cache of last-trade prices for registered symbols,
updated on every tick. The watcher reads from this cache for faster exits.
"""

import asyncio
import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger("alphadesk.quant.stream")

_ws_client: Optional[object] = None
_prices: dict[str, float] = {}
_spreads: dict[str, tuple[float, float]] = {}  # symbol → (bid, ask)
_lock = threading.Lock()
_connected = threading.Event()
_running = False
_tasks: list[asyncio.Task] = []


def get_prices(symbols: list[str] | None = None) -> dict[str, float]:
    """Snapshot of current prices. None = all registered symbols."""
    with _lock:
        if symbols is None:
            return dict(_prices)
        return {s: _prices[s] for s in (sym.upper() for sym in symbols) if s in _prices}


def get_price(symbol: str) -> Optional[float]:
    sym = symbol.upper()
    with _lock:
        return _prices.get(sym)


def get_spread(symbol: str) -> Optional[tuple[float, float]]:
    sym = symbol.upper()
    with _lock:
        return _spreads.get(sym)


def is_connected() -> bool:
    return _connected.is_set()


async def _on_trade(data):
    try:
        sym = data.symbol.upper()
        price = float(data.price)
        with _lock:
            _prices[sym] = price
    except Exception:
        pass


async def _on_quote(data):
    try:
        sym = data.symbol.upper()
        bid = float(data.bid_price) if data.bid_price > 0 else 0.0
        ask = float(data.ask_price) if data.ask_price > 0 else 0.0
        if bid > 0 and ask > 0:
            with _lock:
                _spreads[sym] = (bid, ask)
    except Exception:
        pass


async def start_stream(symbols: list[str] | None = None):
    """Start the Alpaca WebSocket stream in a background task.
    Registers initial symbols; more are added via register().
    """
    global _ws_client, _running
    if _running:
        return

    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        log.warning("Alpaca stream: missing API keys — streaming disabled")
        return

    from alpaca.data.live import StockDataStream

    _running = True
    _ws_client = StockDataStream(key, secret)

    _ws_client.subscribe_trades(_on_trade, *(symbols or []))
    _ws_client.subscribe_quotes(_on_quote, *(symbols or []))

    async def _run():
        import concurrent.futures
        while _running:
            try:
                _connected.set()
                log.info("Alpaca stream connected")
                with concurrent.futures.ThreadPoolExecutor(1) as pool:
                    await asyncio.get_running_loop().run_in_executor(
                        pool, _ws_client.run)
            except Exception as exc:
                _connected.clear()
                log.warning("Alpaca stream disconnected: %s — reconnecting in 5s", exc)
                await asyncio.sleep(5)

    return asyncio.create_task(_run())


async def stop_stream():
    global _running
    _running = False
    _connected.clear()
    if _ws_client:
        try:
            await _ws_client.stop()
        except Exception:
            pass


def register(symbol: str):
    """Add a symbol to the stream's subscription. Safe to call from any thread."""
    sym = symbol.upper()
    with _lock:
        if sym not in _prices:
            _prices[sym] = 0.0
    if _ws_client and _connected.is_set():
        try:
            _ws_client.subscribe_trades(_on_trade, sym)
        except Exception:
            pass


def register_many(symbols: list[str]):
    for s in symbols:
        register(s)
