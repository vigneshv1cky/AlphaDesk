"""Dashboard — FastAPI serving the shadcn/ui SPA + JSON API.

Auth: HTTP Basic enforced by middleware on EVERY route (API, SPA, assets) —
fail-closed if ADMIN_USERNAME/ADMIN_PASSWORD are unset.
Frontend: built by `pnpm build` in alphadesk/ui → alphadesk/app/static/.
"""

import base64
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from alphadesk.knowledge.graph import Graph
from alphadesk.ledger import store

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="AlphaDesk")


@app.middleware("http")
async def _basic_auth(request: Request, call_next):
    user = os.environ.get("ADMIN_USERNAME", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not user or not password:
        return Response("auth not configured", status_code=503)
    header = request.headers.get("Authorization", "")
    ok = False
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header[6:]).decode()
            u, _, p = decoded.partition(":")
            ok = secrets.compare_digest(u.encode(), user.encode()) and secrets.compare_digest(
                p.encode(), password.encode()
            )
        except Exception:
            ok = False
    if not ok:
        return Response(
            "unauthorized", status_code=401, headers={"WWW-Authenticate": "Basic realm=alphadesk"}
        )
    return await call_next(request)


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@app.get("/api/picks")
def api_picks(limit: int = 30):
    return {"picks": store.recent(limit)}


@app.get("/api/picks/{pick_id}")
def api_pick(pick_id: int):
    pick = store.get_pick(pick_id)
    if not pick:
        raise HTTPException(404, "no such pick")
    return pick


@app.get("/api/stats")
def api_stats():
    return store.stats()


@app.get("/api/funnel")
def api_funnel(limit: int = 30):
    from alphadesk.app import scheduler
    return {"paused": scheduler.paused(), "windows": store.funnel_recent(limit)}


@app.get("/api/tokens")
def api_tokens(days: int = 1):
    return {"days": days, "usage": store.token_summary(days)}


@app.get("/api/graph")
def api_graph():
    return Graph.default().summary()


# ---------------------------------------------------------------------------
# SPA — static bundle with index fallback (client handles the rest)
# ---------------------------------------------------------------------------

@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path:
        candidate = (_STATIC / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(_STATIC.resolve()):
            return FileResponse(candidate)
    index = _STATIC / "index.html"
    if not index.is_file():
        return Response(
            "UI bundle missing — run `pnpm build` in alphadesk/ui", status_code=503
        )
    return FileResponse(index)
