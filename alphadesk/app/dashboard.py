"""Dashboard — FastAPI, HTTP Basic Auth on every route (fail-closed).

/            ledger + funnel overview
/pick/{id}   the full agent conversation for one decision, rendered as chat
/api/*       JSON endpoints
"""

import html
import json
import os
import secrets

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from alphadesk.knowledge.graph import Graph
from alphadesk.ledger import store

_security = HTTPBasic()


def _require_auth(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
    user = os.environ.get("ADMIN_USERNAME", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not user or not password:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth not configured")
    ok_user = secrets.compare_digest(credentials.username.encode(), user.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), password.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


app = FastAPI(title="AlphaDesk", dependencies=[Depends(_require_auth)])

_STYLE = """
<style>
  body{font-family:ui-monospace,SFMono-Regular,monospace;background:#0d1117;
       color:#c9d1d9;padding:1.5em;max-width:1080px;margin:auto;font-size:14px}
  a{color:#58a6ff;text-decoration:none} a:hover{text-decoration:underline}
  table{border-collapse:collapse;width:100%;margin:.6em 0}
  td,th{border:1px solid #30363d;padding:5px 9px;font-size:13px;text-align:left}
  th{background:#161b22} .dim{color:#8b949e} .pos{color:#3fb950} .neg{color:#f85149}
  .bubble{border:1px solid #30363d;border-radius:8px;padding:.8em 1em;margin:.7em 0}
  .who{font-weight:bold;margin-bottom:.35em;font-size:12px;letter-spacing:.06em}
  .triage{border-left:4px solid #d29922}  .triage .who{color:#d29922}
  .brief{border-left:4px solid #8b949e;background:#11151c} .brief .who{color:#8b949e}
  .analyst{border-left:4px solid #58a6ff} .analyst .who{color:#58a6ff}
  .skeptic{border-left:4px solid #f85149} .skeptic .who{color:#f85149}
  .arbiter{border-left:4px solid #3fb950} .arbiter .who{color:#3fb950}
  .solo{border-left:4px solid #bc8cff}    .solo .who{color:#bc8cff}
  .flag{border-left:4px solid #db6d28;background:#1c1108} .flag .who{color:#db6d28}
  .tag{display:inline-block;background:#21262d;border-radius:4px;padding:1px 7px;
       margin-right:.4em;font-size:12px}
  details{margin:.3em 0} summary{cursor:pointer;color:#8b949e}
  h2,h3{font-weight:600} .score{font-size:15px;font-weight:bold}
</style>"""


def _esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


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
# HTML — the conversation view
# ---------------------------------------------------------------------------

def _bubble(css: str, who: str, body_html: str) -> str:
    return f'<div class="bubble {css}"><div class="who">{who}</div>{body_html}</div>'


def _exit_date(ts: str, session: str, horizon_days: int) -> str:
    """Approximate exit date: entry day (next trading day if decided closed)
    + horizon trading days, skipping weekends."""
    from datetime import datetime, timedelta
    try:
        d = datetime.fromisoformat(ts)
    except ValueError:
        return "?"
    if session != "OPEN":
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    remaining = horizon_days
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            remaining -= 1
    return d.strftime("%a %b %d")


def _call_box(p: dict) -> str:
    """THE CALL — the decision, stated plainly."""
    direction = p["direction"]
    css = "pos" if direction == "LONG" else "neg"
    arrow = "▲" if direction == "LONG" else "▼"
    entry = f"${p['entry_price']}" if p.get("entry_price") else "next market open"
    exit_day = _exit_date(p["ts"], p["session"], int(p["horizon_days"]))
    status = ("✅ ON THE BOOK" if p["approved"]
              else "❌ REJECTED by the arbiter — recorded as a counterfactual")
    return (
        f"<div class='bubble' style='border:2px solid #30363d;background:#161b22'>"
        f"<div class='who'>THE CALL</div>"
        f"<p class='score'><span class='{css}'>{arrow} {direction}</span> "
        f"{_esc(p['symbol'])} · hold <b>{_esc(p['horizon_days'])} trading days</b> "
        f"(≈ until {exit_day})</p>"
        f"<p>entry: {entry} · conviction {p['adjusted_score'] if p['adjusted_score'] is not None else p['score']:.0f}/100 "
        f"· confidence {p['confidence']:.0f}/100</p>"
        f"<p>{status}</p></div>"
    )


@app.get("/pick/{pick_id}", response_class=HTMLResponse)
def pick_page(pick_id: int):
    p = store.get_pick(pick_id)
    if not p:
        raise HTTPException(404, "no such pick")
    debate = p.get("debate") or {}
    briefs = p.get("briefs") or []
    tags = p.get("model_tags") or {}

    parts: list[str] = [f"<html><head><title>#{pick_id} {_esc(p['symbol'])}</title>{_STYLE}</head><body>"]
    alpha = p.get("alpha_net")
    alpha_html = (f"<span class='{'pos' if alpha > 0 else 'neg'}'>{alpha:+.2f}%</span>"
                  if alpha is not None else "<span class='dim'>pending</span>")
    parts.append(
        f"<p><a href='/'>← ledger</a></p>"
        f"<h2>#{pick_id} · {_esc(p['symbol'])} {_esc(p['direction'])} "
        f"{_esc(p['horizon_days'])}d</h2>"
        f"<p><span class='tag'>{_esc(p['arm'])}</span>"
        f"<span class='tag'>{_esc(p['edge'] or '—')}</span>"
        f"<span class='tag'>{_esc(p['trigger_src'])}</span>"
        f"<span class='tag'>session {_esc(p['session'])}</span>"
        f"<span class='tag'>{_esc(p['ts'][:16])}</span></p>"
        f"<p class='score'>score {p['score']:.0f} → "
        f"{p['adjusted_score'] if p['adjusted_score'] is not None else '—'}"
        f" · confidence {p['confidence']:.0f}"
        f" · verdict {_esc(p['verdict'] or '—')}"
        f" · {'✅ ON THE BOOK' if p['approved'] else '❌ rejected'}"
        f" · net alpha {alpha_html}</p>"
    )

    parts.append(_call_box(p))

    if p.get("triage_reason"):
        parts.append(_bubble("triage", "TRIAGE — why this deserved the committee",
                             f"<p>{_esc(p['triage_reason'])}</p>"))

    for b in briefs:
        facts = "".join(f"<li>{_esc(f.get('fact', f))}</li>" for f in (b.get("key_facts") or []))
        parts.append(_bubble(
            "brief", f"{_esc(b.get('kind', 'brief')).upper()} BRIEF (subagent)",
            f"<p>{_esc(b.get('summary'))}</p>" + (f"<ul>{facts}</ul>" if facts else ""),
        ))

    if p.get("thesis"):
        parts.append(_bubble(
            "analyst", f"ANALYST ({_esc(tags.get('analyst', '?'))}) — thesis",
            f"<p>{_esc(p['thesis'])}</p>"
            f"<p class='dim'>score {p['score']:.0f} · horizon {_esc(p['horizon_days'])}d</p>",
        ))

    for c in debate.get("concerns", []):
        parts.append(_bubble(
            "skeptic", f"SKEPTIC ({_esc(tags.get('skeptic', '?'))}) — attack",
            f"<p><b>{_esc(c.get('claim'))}</b></p><p class='dim'>{_esc(c.get('evidence'))}</p>",
        ))

    for flag in debate.get("fact_flags", []):
        parts.append(_bubble("flag", "FACT-CHECK (code)", f"<p>{_esc(flag)}</p>"))

    reb = debate.get("rebuttal")
    if reb:
        parts.append(_bubble(
            "analyst", "ANALYST — rebuttal",
            f"<p>{_esc(reb.get('rebuttal'))}</p>"
            f"<p class='dim'>revised score {_esc(reb.get('revised_score'))}"
            f" · conceded: {_esc(reb.get('concede'))}</p>",
        ))

    if debate.get("arbiter_summary"):
        parts.append(_bubble(
            "arbiter", f"ARBITER ({_esc(tags.get('arbiter', '?'))}) — verdict",
            f"<p>{_esc(debate['arbiter_summary'])}</p>"
            f"<p class='dim'>adjusted {p['adjusted_score']} · confidence {p['confidence']:.0f}"
            f" · {_esc(p['verdict'])} · approved: {bool(p['approved'])}</p>",
        ))

    if p["arm"] == "SOLO" and p.get("thesis"):
        parts.append(_bubble(
            "solo", f"SOLO ANALYST ({_esc(tags.get('solo', '?'))}) — independent take",
            "<p class='dim'>(control arm — worked the same evidence blind to the committee)</p>",
        ))

    parts.append(f"<details><summary>raw JSON</summary><pre>{_esc(json.dumps(p, indent=2, default=str))}</pre></details>")
    parts.append("</body></html>")
    return "".join(parts)


def _alpha_cell(alpha) -> str:
    if alpha is None:
        return "<span class='dim'>…</span>"
    css = "pos" if alpha > 0 else "neg"
    return f"<span class='{css}'>{alpha:+.2f}%</span>"


@app.get("/", response_class=HTMLResponse)
def index():
    rows = store.recent(25)
    body = "".join(
        f"<tr><td><a href='/pick/{r['id']}'>#{r['id']}</a></td><td>{_esc(r['ts'][11:16])}</td>"
        f"<td><b><a href='/pick/{r['id']}'>{_esc(r['symbol'])}</a></b></td>"
        f"<td><span class='{'pos' if r['direction'] == 'LONG' else 'neg'}'>"
        f"{'▲ LONG' if r['direction'] == 'LONG' else '▼ SHORT'}</span>"
        f" · hold {_esc(r['horizon_days'])}d</td>"
        f"<td>{r['score']:.0f}→{r['adjusted_score'] if r['adjusted_score'] is not None else '—'}</td>"
        f"<td>{_esc(r['verdict'] or '')}</td><td>{_esc(r['arm'])}</td><td>{_esc(r['edge'] or '')}</td>"
        f"<td>{'✅' if r['approved'] else '❌'}</td>"
        f"<td>{_alpha_cell(r['alpha_net'])}</td></tr>"
        for r in rows
    )
    s = store.stats()["total"]
    windows = store.funnel_recent(6)
    fun_rows = ""
    for w in windows:
        try:
            skips = json.loads(w.get("skip_reasons") or "[]")
        except Exception:
            skips = []
        skip_html = "".join(
            f"<li><b>{_esc(sk.get('symbol'))}</b>: {_esc(sk.get('reason'))}</li>" for sk in skips
        )
        fun_rows += (
            f"<tr><td>{_esc(w['window_ts'][11:16])}</td><td>{w['candidates']}</td>"
            f"<td>{w['picked']}</td>"
            f"<td><details><summary>{w['skipped']} skipped</summary><ul>{skip_html}</ul></details></td></tr>"
        )
    tokens = store.token_summary(1)
    tok = " · ".join(f"{t['role']}:{t['output_tok'] // 1000}k" for t in tokens[:6])
    graph = Graph.default().summary()

    return f"""<html><head><title>AlphaDesk</title>{_STYLE}
      <meta http-equiv="refresh" content="60"></head><body>
      <h2>AlphaDesk — the desk, live</h2>
      <p class='dim'>picks {s['picks']} · graded {s['graded']} · avg net alpha
      {s['avg_alpha_net'] if s['avg_alpha_net'] is not None else '—'}% ·
      graph {graph.get('articles', '?')} articles / {graph.get('relations', '?')} relations ·
      tokens today {tok or '—'}</p>
      <h3>Decisions <span class='dim'>(click any row to read the agents' conversation)</span></h3>
      <table><tr><th>id</th><th>time</th><th>sym</th><th>prediction (direction · hold)</th><th>score</th>
      <th>verdict</th><th>arm</th><th>edge</th><th>book</th><th>alpha</th></tr>{body}</table>
      <h3>Attention windows <span class='dim'>(what triage saw and why it skipped)</span></h3>
      <table><tr><th>time</th><th>candidates</th><th>picked</th><th>skips (expand)</th></tr>{fun_rows}</table>
      <p><a href='/api/stats'>stats</a> · <a href='/api/tokens'>tokens</a> ·
         <a href='/api/graph'>graph</a> · <a href='/api/funnel'>funnel json</a></p>
      </body></html>"""
