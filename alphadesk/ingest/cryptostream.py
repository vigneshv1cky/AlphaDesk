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
                        # The WHOLE row, not just the price. Coinbase's
                        # ticker already carries open_24h, volume_24h and the
                        # day's range, which is every column the movers panel
                        # renders — so one subscription answers both the strip
                        # and the panel, and the panel stops needing a poll.
                        def _f(key):
                            v = msg.get(key)
                            try:
                                return float(v) if v is not None else None
                            except (TypeError, ValueError):
                                return None

                        open24 = _f("open_24h")
                        price = float(px)
                        self._last[pid] = {
                            "symbol": pid,
                            "price": price,
                            # Rolling 24h, the figure every crypto venue quotes
                            # — and the one the panel already claims to show.
                            "change_pct": (round(100.0 * (price - open24) / open24, 2)
                                           if open24 else None),
                            "volume": _f("volume_24h"),
                            "high_24h": _f("high_24h"),
                            "low_24h": _f("low_24h"),
                            "bid": _f("best_bid"),
                            "ask": _f("best_ask"),
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


_board_subscribed = False


def ensure_board_subscribed() -> None:
    """Hold one reference per universe product, once, for the process life.

    Deliberately not reference counted like the readers are. The crypto board
    is a fixture of the terminal rather than something opened and closed, and
    twenty products on an existing socket cost nothing — where acquiring and
    releasing them per request would tear the subscription down between polls
    and guarantee the next one found nothing.
    """
    global _board_subscribed
    if _board_subscribed:
        return
    for pid in board_products():
        stream.acquire(pid)
    _board_subscribed = True


def board(top: int = 20) -> dict:
    """{all, most_active, gainers, losers} straight off the live stream.

    Same shape ingest/prices.crypto_movers() returns, so the panel does not
    care which produced it — but two things are better here than in the REST
    path it replaces.

    The volume is REAL. Alpaca's crypto venue prints about 11 BTC a day, which
    made "Most Active" honest only as busiest-on-this-feed; Coinbase's
    volume_24h for the same window is over 18,000. Ranking by it now means what
    a reader assumes it means.

    And the change is the venue's own rolling 24h open rather than a figure
    assembled from hourly bars, so it agrees with what every other crypto
    screen shows.

    Returns {} when nothing has arrived yet — the caller falls back rather than
    rendering an empty board.
    """
    names = labels()
    rows = []
    for pid in board_products():
        tick = stream.latest(pid)
        if not tick or tick.get("stale") or tick.get("change_pct") is None:
            continue
        price = tick["price"]
        vol = tick.get("volume") or 0.0
        dp = 6 if price < 1 else (4 if price < 100 else 2)
        rows.append({
            "symbol": pid,
            "name": names.get(pid, pid),
            "price": round(price, dp),
            "change_pct": tick["change_pct"],
            "volume": int(vol),
            "dollar_volume": vol * price,
            # No spark: a stream carries the present, not a series. The REST
            # path built one from hourly bars, and inventing a shape from a
            # single point would be worse than leaving the cell empty — the
            # Sparkline primitive already renders nothing below two points.
            "spark": [],
        })
    if not rows:
        return {}
    from alphadesk.ingest.prices import _rank_crypto
    return _rank_crypto(rows, top)


def board_products() -> list[str]:
    """CRYPTO_UNIVERSE in Coinbase's product form.

    The universe is written in Alpaca's notation (BTC/USD) because the REST
    fallback still speaks it; Coinbase uses a hyphen. All twenty were verified
    present and online on Coinbase, which lists 398 USD pairs.
    """
    from alphadesk.config import CRYPTO_UNIVERSE
    out = []
    for entry in CRYPTO_UNIVERSE:
        sym = entry.partition(":")[0].strip().upper().replace("/", "-")
        if sym:
            out.append(sym)
    return out


def labels() -> dict[str, str]:
    """Coinbase product id -> the display name from config."""
    from alphadesk.config import CRYPTO_UNIVERSE
    out = {}
    for entry in CRYPTO_UNIVERSE:
        sym, _, label = entry.partition(":")
        pid = sym.strip().upper().replace("/", "-")
        if pid:
            out[pid] = (label or pid).strip()
    return out


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
