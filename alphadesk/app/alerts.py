"""Notifications — fire-and-forget webhook alerts (Telegram/Slack/Discord).

Set ALERTS_WEBHOOK_URL to a generic incoming-webhook endpoint; every notifiable
event posts a small JSON {text: ...} payload. Never blocks or raises: send happens
on a daemon thread, and a failure is silently ignored so alerts can't take down the
desk. No URL configured = no-op.
"""

import json
import logging
import os
import threading
import urllib.request

log = logging.getLogger("alphadesk.alerts")

_WEBHOOK_URL = os.environ.get("ALERTS_WEBHOOK_URL", "").strip()


def enabled() -> bool:
    return bool(_WEBHOOK_URL)


def notify(text: str, level: str = "info") -> None:
    if not _WEBHOOK_URL:
        return
    prefix = {"info": "ℹ️", "warn": "⚠️", "error": "🚨", "pick": "📈"}.get(level, "ℹ️")

    def _send():
        try:
            payload = json.dumps({"text": f"{prefix} AlphaDesk · {text}"}).encode("utf-8")
            req = urllib.request.Request(
                _WEBHOOK_URL, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception:
            pass   # alerts are best-effort — never let them break the desk

    try:
        threading.Thread(target=_send, daemon=True).start()
    except Exception:
        pass
