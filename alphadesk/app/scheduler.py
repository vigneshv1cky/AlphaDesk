"""Healthbeat — simple liveness signal for the /healthz endpoint.
Called by the grader, watcher, and portfolio loops in dashboard mode.
"""

import logging
import time

log = logging.getLogger("alphadesk.scheduler")

_last_heartbeat: float = 0.0


def beat() -> None:
    global _last_heartbeat
    _last_heartbeat = time.monotonic()


def heartbeat_age_s() -> float:
    if _last_heartbeat == 0.0:
        return float("inf")
    return time.monotonic() - _last_heartbeat
