"""Live crypto prices from Coinbase, pushed rather than polled.

WHY A SECOND STREAM AND A SECOND VENUE. ingest/stream.py holds Alpaca's
StockDataStream, which is equities only — it carries no crypto at all. And
Alpaca's crypto venue is too thin to drive a live readout: measured over the
same window, Coinbase pushed 517 BTC ticker updates carrying 25 distinct
prices in 15 seconds where Alpaca's crypto feed produced 12 quotes in 20 and
no trades, against a 24-hour volume of 18,020 BTC to Alpaca's ~11. The ticker
was updating once a minute off yfinance and looked frozen, which it was.

NO CREDENTIALS. Coinbase's market-data websocket is public — no key, no
account, nothing to configure — so this works on a fresh clone. That is a
different posture from the rest of ingest/, and a better one for an open
source terminal.

ONE connection for every product, not one per reader, and it is reference
counted the same way stream.py is: the last reader to leave closes the socket.

WHAT THIS IS NOT. Coinbase is one exchange, not the consolidated crypto tape.
Its price is the Coinbase price. For a glanceable ticker that is the right
trade — it is the venue most quoted prices come from — but nothing here should
present it as a composite.
"""

import json
import logging
import threading
import time
from typing import Optional

log = logging.getLogger("alphadesk.cryptostream")

WS_URL = "wss://ws-feed.exchange.coinbase.com"

# A tick older than this is stale rather than live. Crypto trades around the
# clock, so unlike the equity stream a quiet symbol here means the connection
# is unwell, not that the market is shut.
TICK_STALE_AFTER_S = 20.0


class _CryptoStream:
    """The process-wide Coinbase connection, built on first subscriber."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._loop = None
        self._refs: dict[str, int] = {}
        self._last: dict[str, dict] = {}
        self._failed = False
        self._connected = False
        self._wanted_changed = threading.Event()

    # ── connection ──────────────────────────────────────────────────────────

    def _ensure_running(self) -> bool:
        if self._failed:
            return False
        if self._thread and self._thread.is_alive():
            return True
        try:
            import websockets  # noqa: F401
        except Exception as exc:                      # pragma: no cover
            log.info("crypto stream unavailable (no websockets): %s", exc)
            self._failed = True
            return False
        self._thread = threading.Thread(target=self._run, name="alphadesk-cryptostream",
                                        daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        import asyncio
        try:
            asyncio.run(self._pump())
        except Exception as exc:
            # Losing this socket must not take the process with it: the tape
            # still polls, so a dead stream degrades to the old behaviour.
            log.info("crypto stream ended: %s", exc)
        finally:
            self._connected = False

    async def _pump(self) -> None:
        import asyncio

        import websockets
        while True:
            with self._lock:
                products = sorted(self._refs)
            if not products:
                return                                # nobody watching; let it die
            try:
                async with websockets.connect(WS_URL, open_timeout=15) as ws:
                    await ws.send(json.dumps({
                        "type": "subscribe", "product_ids": products,
                        "channels": ["ticker"],
                    }))
                    self._connected = True
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        msg = json.loads(raw)
                        if msg.get("type") != "ticker":
                            continue
                        pid = msg.get("product_id")
                        px = msg.get("price")
                        if not pid or px is None:
                            continue
                        self._last[pid] = {
                            "symbol": pid,
                            "price": float(px),
                            "at": str(msg.get("time", "")),
                            "received": time.time(),
                        }
                        # Resubscribe when the watched set changes rather than
                        # holding a socket for products nobody is reading.
                        with self._lock:
                            current = sorted(self._refs)
                        if current != products:
                            break
            except Exception as exc:
                self._connected = False
                log.debug("crypto stream reconnecting: %s", exc)
                await asyncio.sleep(3)

    # ── subscription ────────────────────────────────────────────────────────

    def acquire(self, product: str) -> bool:
        pid = product.upper()
        with self._lock:
            self._refs[pid] = self._refs.get(pid, 0) + 1
        return self._ensure_running()

    def release(self, product: str) -> None:
        pid = product.upper()
        with self._lock:
            n = self._refs.get(pid, 0)
            if n <= 1:
                self._refs.pop(pid, None)
                self._last.pop(pid, None)
            else:
                self._refs[pid] = n - 1

    # ── reading ─────────────────────────────────────────────────────────────

    def latest(self, product: str) -> Optional[dict]:
        tick = self._last.get(product.upper())
        if not tick:
            return None
        out = dict(tick)
        out["age_s"] = round(time.time() - tick["received"], 2)
        out["stale"] = out["age_s"] > TICK_STALE_AFTER_S
        return out

    def status(self) -> dict:
        with self._lock:
            return {
                "connected": self._connected,
                "available": not self._failed,
                "products": dict(sorted(self._refs.items())),
            }


stream = _CryptoStream()


def crypto_products() -> list[str]:
    """The MARKET_TAPE entries Coinbase can serve.

    The tape already writes crypto in Coinbase's own product form — BTC-USD —
    so the two need no translation. Anything else on the tape (^GSPC, CL=F,
    EURUSD=X) is not a crypto pair and is left to the polled path.
    """
    from alphadesk.config import MARKET_TAPE
    out = []
    for entry in MARKET_TAPE:
        sym = entry.partition(":")[0].strip().upper()
        if sym.endswith("-USD") and not sym.startswith("^"):
            out.append(sym)
    return out
