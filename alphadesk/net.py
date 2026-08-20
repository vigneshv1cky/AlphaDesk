"""Socket deadlines for vendor SDKs that ship without them.

`alpaca-py`'s REST client takes no timeout argument and calls requests'
`Session.request` without one — and requests' default is to wait forever. That
is not a slow request, it is an unbounded one.

It matters because of how this app serves: FastAPI runs every endpoint here as
a sync `def`, which Starlette hands to a 40-worker threadpool. A hung upstream
therefore does not fail a request — it parks a worker until the OS gives up on
the socket. Forty of those and the API stops answering entirely, `/healthz`
included, because that is a sync endpoint too. The uptime check would fire only
once the terminal was already wedged.

Six endpoints reach an Alpaca SDK on the request path (quote, movers, chart,
tape and both filings routes), so the exposure is real rather than theoretical.

yfinance is not covered here: it already defaults to a 30-second timeout
internally, so it is bounded even though it is slow.
"""

from __future__ import annotations

import logging
import os
from typing import Any, TypeVar

log = logging.getLogger("alphadesk.net")

# Generous enough that a healthy-but-busy upstream still answers, short enough
# that a dead one frees the worker while the reader is still looking at the
# page. Callers already degrade on failure — a missing tile beats a hung board.
ALPACA_TIMEOUT_S = float(os.environ.get("ALPACA_TIMEOUT_S", "15"))

_MARK = "_alphadesk_bounded"

T = TypeVar("T")


def bound_timeout(client: T, timeout_s: float = ALPACA_TIMEOUT_S) -> T:
    """Give an alpaca-py client a default socket timeout. Returns the client.

    Wraps the session's `request` rather than subclassing or passing an
    argument: the SDK constructs its own `requests.Session` and exposes no seam
    for one, so this is the only place a deadline can be attached without
    forking the vendor.

    `setdefault` rather than an overwrite, so an SDK call that does pass its own
    timeout keeps it. Idempotent — wrapping twice would nest the closures and
    make the real timeout impossible to reason about.
    """
    session = getattr(client, "_session", None)
    if session is None:
        # A future SDK version could rename it. Degrade to today's behaviour
        # rather than crashing, but say so — this is a guarantee going quiet.
        log.warning("%s exposes no _session; requests from it stay unbounded",
                    type(client).__name__)
        return client
    if getattr(session, _MARK, False):
        return client

    original = session.request

    def bounded(method: str, url: str, **kwargs: Any):
        kwargs.setdefault("timeout", timeout_s)
        return original(method, url, **kwargs)

    session.request = bounded
    setattr(session, _MARK, True)
    return client
