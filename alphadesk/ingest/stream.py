"""Real-time trades, pushed rather than polled.

ONE upstream connection, fanned out. Alpaca's free tier allows a single
concurrent market-data websocket per account, so this holds it for the whole
process and every reader shares it — a per-request connection would work for
exactly one reader and then start failing for the rest.

Subscriptions are REFERENCE COUNTED against the symbols people are actually
looking at. A terminal showing NVDA subscribes to NVDA; close the tab and the
last reader releases it and the upstream subscription goes with it. Nothing
here sweeps a universe or holds symbols nobody has open, which is the same
lazy-and-per-symbol rule the rest of ingest/prices.py follows.

WHAT THIS DOES NOT PROMISE. The feed is IEX, which carries a few percent of
consolidated volume: measured over 26 seconds mid-session, NVDA pushed 23
trades, AAPL 11, and ENTA pushed nothing at all. A silent symbol here means
"this feed saw no print", NOT "the stock did not trade" and certainly not
"the market is closed" — the same distinction _coverage_stats draws for the
chart. Anything rendering these ticks has to say when it last heard one
rather than let an old price sit there looking current.
"""

import logging
import os
import threading
import time
from typing import Any, Optional

log = logging.getLogger("alphadesk.stream")

# A tick older than this is stale rather than live. Not a reconnect trigger —
# a quiet symbol on IEX is normal — just how long a price may be presented as
# current before the UI should say when it actually arrived.
TICK_STALE_AFTER_S = 30.0


class _MarketStream:
    """The process-wide upstream connection.

    Built lazily on the first subscriber, so an instance nobody is watching —
    a cron run, the MCP server, the test suite — never opens a socket.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: Any = None
        self._thread: Optional[threading.Thread] = None
        self._refs: dict[str, int] = {}
        self._last: dict[str, dict] = {}
        self._failed = False

    # ── upstream ────────────────────────────────────────────────────────────

    def _ensure_running(self) -> bool:
        """Start the connection if it is not up. Returns False when streaming
        is unavailable (no keys, SDK missing, a previous hard failure), which
        callers treat as "no live data" and fall back to polling rather than
        as an error worth surfacing."""
        if self._failed:
            return False
        if self._stream is not None:
            return True
        try:
            from alpaca.data.live import StockDataStream
        except Exception as exc:                      # pragma: no cover
            log.info("streaming unavailable (no SDK): %s", exc)
            self._failed = True
            return False
        key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            log.info("streaming unavailable (no ALPACA keys)")
            self._failed = True
            return False
        try:
            self._stream = StockDataStream(key, secret)
            self._thread = threading.Thread(
                target=self._run, name="alphadesk-stream", daemon=True)
            self._thread.start()
            return True
        except Exception as exc:
            log.warning("could not start market stream: %s", exc)
            self._failed = True
            self._stream = None
            return False

    def _run(self) -> None:
        try:
            self._stream.run()
        except Exception as exc:
            # Includes a normal stop(). Losing the socket must not take the
            # process with it: the REST path still answers every panel, so a
            # dead stream degrades to the polling behaviour rather than an
            # outage.
            log.info("market stream ended: %s", exc)
        finally:
            with self._lock:
                self._stream = None
                self._thread = None
                self._refs.clear()

    async def _on_trade(self, t: Any) -> None:
        try:
            self._last[str(t.symbol).upper()] = {
                "symbol": str(t.symbol).upper(),
                "price": float(t.price),
                "size": int(getattr(t, "size", 0) or 0),
                "at": str(getattr(t, "timestamp", "")),
                "received": time.time(),
            }
        except Exception:                             # a malformed tick is not fatal
            pass

    # ── subscription ────────────────────────────────────────────────────────

    def acquire(self, symbol: str) -> bool:
        """Take a reference on `symbol`, subscribing upstream if it is the
        first. Safe to call repeatedly."""
        sym = symbol.upper()
        with self._lock:
            if not self._ensure_running():
                return False
            n = self._refs.get(sym, 0)
            self._refs[sym] = n + 1
            if n:
                return True
        try:
            self._stream.subscribe_trades(self._on_trade, sym)
            return True
        except Exception as exc:
            log.warning("subscribe failed for %s: %s", sym, exc)
            with self._lock:
                self._refs[sym] = max(0, self._refs.get(sym, 1) - 1)
            return False

    def release(self, symbol: str) -> None:
        """Drop a reference; unsubscribe upstream when the last reader goes."""
        sym = symbol.upper()
        with self._lock:
            n = self._refs.get(sym, 0)
            if n <= 1:
                self._refs.pop(sym, None)
            else:
                self._refs[sym] = n - 1
                return
            stream = self._stream
        if stream is None:
            return
        try:
            stream.unsubscribe_trades(sym)
        except Exception as exc:
            log.debug("unsubscribe failed for %s: %s", sym, exc)
        self._last.pop(sym, None)

    # ── reading ─────────────────────────────────────────────────────────────

    def latest(self, symbol: str) -> Optional[dict]:
        """The most recent trade seen for `symbol`, or None if this feed has
        not printed one since we subscribed. None is a real answer — see the
        module docstring — not a failure to report."""
        tick = self._last.get(symbol.upper())
        if not tick:
            return None
        out = dict(tick)
        out["age_s"] = round(time.time() - tick["received"], 2)
        out["stale"] = out["age_s"] > TICK_STALE_AFTER_S
        return out

    def status(self) -> dict:
        with self._lock:
            return {
                "connected": self._stream is not None,
                "available": not self._failed,
                # Counts, not just names: "NVDA is subscribed" and "NVDA is
                # subscribed eleven times because nothing is releasing" look
                # identical without them.
                "symbols": dict(sorted(self._refs.items())),
            }


stream = _MarketStream()
