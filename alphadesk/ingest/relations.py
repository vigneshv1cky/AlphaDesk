"""Relationship FACTS gathered in code — no LLM discovery.

Three sources, all evidence-backed:
  • SEC EDGAR full-text search: 10-Ks (incl. amendments) that name the shocked
    company near customer/supplier keywords — public companies MUST disclose
    major customers, so these are verified links with the filing as citation.
  • Polygon related-companies: a factual peer/competitor set.
  • news-stated relations (SUPPLIES|COMPETES|PARTNERS) the enrichment already
    extracts from article text — persisted to `relation_facts` (they used to
    evaporate with the article dicts).

The connections desk consumes these as CANDIDATES + evidence; an LLM still judges
direction/strength (that's judgment, not discovery), but it never has to search
or recall — design law #1: code owns facts, agents own judgment.
"""

import html as _html
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from alphadesk.config import DATA_DIR, in_universe
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.relations")

# SEC fair-access policy: declared UA with a plausible contact — a localhost/bogus
# address gets a hard 403 on www.sec.gov downloads (verified).
_UA = {"User-Agent": "AlphaDesk/1.0 (alphadesk@example.com)"}
_POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")
_MAX_EDGAR_DOCS = 8          # filings to open per shocked company (politeness + latency)
_EDGAR_SLEEP_S = 0.3         # SEC asks for ≤10 req/s and a declared UA


def _get(url: str, timeout: float = 20) -> bytes | None:
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.debug("relations fetch failed %s: %s", url[:90], exc)
        return None


# ---------------------------------------------------------------------------
# EDGAR ticker/name map (shared by the query side and the result side)
# ---------------------------------------------------------------------------

_TICKERS_CACHE = DATA_DIR / "edgar_tickers.json"
_TICKERS_MAX_AGE_S = 7 * 24 * 3600
_ticker_map: dict | None = None   # {"by_ticker": {T: name}, "by_cik": {cik: ticker}}


def _load_ticker_map() -> dict:
    global _ticker_map
    if _ticker_map is not None:
        return _ticker_map
    fresh = (_TICKERS_CACHE.exists()
             and time.time() - _TICKERS_CACHE.stat().st_mtime < _TICKERS_MAX_AGE_S)
    data = None
    if fresh:
        try:
            data = json.loads(_TICKERS_CACHE.read_text())
        except Exception:
            data = None
    if data is None:
        raw = _get("https://www.sec.gov/files/company_tickers.json")
        if raw:
            try:
                rows = json.loads(raw)
                data = {
                    "by_ticker": {r["ticker"].upper(): r["title"] for r in rows.values()},
                    # str keys: the JSON cache round-trip stringifies int keys — the
                    # lookup must use the same type or every cache-hit returns None
                    "by_cik": {str(int(r["cik_str"])): r["ticker"].upper() for r in rows.values()},
                }
                _TICKERS_CACHE.write_text(json.dumps(data))
            except Exception as exc:
                log.warning("EDGAR ticker map parse failed: %s", exc)
                data = None
    _ticker_map = data or {"by_ticker": {}, "by_cik": {}}
    return _ticker_map or {"by_ticker": {}, "by_cik": {}}


# ---------------------------------------------------------------------------
# EDGAR customer/supplier links from 10-K text
# ---------------------------------------------------------------------------

# A named-company mention only counts as a RELATIONSHIP when the SAME SENTENCE
# describes a business link — and even then, not every co-occurrence is one:
# "customers' personal data" (possessive, their OWN customers) and vendor lists
# ("managed by third parties including Amazon, Apple, Facebook") are not links.
_LINK_RES = [
    ("CUSTOMER_OF", re.compile(
        r"\b(customer|client)s?\b(?!\s*['’])"                       # not possessive
        r"|accounted for [^.]{0,40}(revenue|sales)"
        r"|% of (our |total |net )?(revenue|sales)"
        r"|revenue concentration")),
    ("SUPPLIER_OF", re.compile(
        r"\b(supplier|vendor)s?\b(?!\s*['’])"
        r"|sole[- ]source|single[- ]source"
        r"|depends? on|relies on|purchases? from")),
]
# Name sitting in a list of tech giants = platform/vendor context, not a link.
_TECH_LIST_RE = re.compile(
    r"(,\s*(?:amazon|google|microsoft|meta|facebook|oracle|ibm|intel|cisco|salesforce)\b"
    r"|\b(?:amazon|google|microsoft|meta|facebook|oracle|ibm|intel|cisco|salesforce)\s*,)")

_WINDOW = 140   # fallback char window (the sentence bound usually governs)


# EDGAR titles ("NVIDIA CORP", "SUPER MICRO COMPUTER, INC.") are NOT how filings
# write the name in prose — strip corporate suffixes for the search phrase.
_NAME_STRIP = re.compile(
    r"\b(CORP(ORATION)?|INC(ORPORATED)?|LTD\.?|LLC|LLP|L\.P\.|LP|COMPANY|CO\.?|"
    r"HOLDINGS?|GROUP|PLC|LIMITED|USA|U\.S\.A\.)\b,?", re.IGNORECASE)


def _query_name(title: str, ticker: str) -> str:
    name = _NAME_STRIP.sub("", title).strip(" ,.")
    name = re.sub(r"\s{2,}", " ", name)
    return name or ticker


def edgar_customer_links(ticker: str, days: int = 730) -> list[dict]:
    """10-K filings naming `ticker`'s company near customer/supplier keywords →
    [{"symbol", "rel", "evidence", "url"}] where rel is CUSTOMER_OF / SUPPLIER_OF /
    RELATED and symbol is the FILING company (the spillover candidate). The shocked
    company itself is excluded; candidates must be tradable."""
    tmap = _load_ticker_map()
    title = tmap["by_ticker"].get(ticker.upper())
    if not title:
        return []
    name = _query_name(title, ticker)
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc).date().isoformat()
    start = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    # Query per link-type ("NAME" AND "customer" / AND "supplier"): a bare name query
    # returns page-1 relevance dominated by the company's own filings and junk
    # filers — the small suppliers' customer disclosures never surface. The ANDed
    # form returns a tiny, targeted filer set (verified against the live API).
    hits: list[dict] = []
    seen_ids: set[str] = set()
    for link_word in ("customer", "supplier"):
        for page in range(4):   # page the small result sets fully (from=0,10,20,30)
            q = urllib.parse.urlencode({
                "q": f'"{name}" AND "{link_word}"', "forms": "10-K,10-K/A",
                "dateRange": "custom", "startdt": start, "enddt": now,
                "from": page * 10})
            raw = _get(f"https://efts.sec.gov/LATEST/search-index?{q}")
            if not raw:
                break
            try:
                batch = (json.loads(raw).get("hits", {}).get("hits") or [])
            except ValueError:
                break
            for h in batch:
                if h.get("_id") and h["_id"] not in seen_ids:
                    seen_ids.add(h["_id"])
                    hits.append(h)
            if len(batch) < 10:
                break
            time.sleep(_EDGAR_SLEEP_S)
        time.sleep(_EDGAR_SLEEP_S)
    # Prefer full 10-Ks: a 10-K/A amendment carries only the amended sections — the
    # customer-concentration text usually isn't in them. Keep an amendment only
    # when it's the filer's sole form in the result set.
    full_ciks = {h["_source"]["ciks"][0] for h in hits
                 if h.get("_source", {}).get("form") == "10-K"}
    hits = [h for h in hits if h.get("_source", {}).get("form") == "10-K"
            or h["_source"]["ciks"][0] not in full_ciks]

    out: list[dict] = []
    seen: set[str] = set()
    name_l = name.lower()
    for h in hits:
        if len(out) >= _MAX_EDGAR_DOCS:
            break
        src = h.get("_source", {})
        ciks = src.get("ciks") or []
        _id = h.get("_id") or ""
        if not ciks or ":" not in _id:
            continue
        try:
            cik = int(ciks[0])
        except (TypeError, ValueError):
            continue
        sym = tmap["by_cik"].get(str(cik))
        if not sym or sym == ticker.upper() or sym in seen or not in_universe(sym):
            continue
        accession, filename = _id.split(":", 1)
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
               f"{accession.replace('-', '')}/{filename}")
        doc = _get(url)
        time.sleep(_EDGAR_SLEEP_S)   # SEC politeness
        if not doc:
            continue
        # Single-word names match case-SENSITIVELY (filings capitalize the company —
        # "NVIDIA", "Apple Inc." — which kills lowercase platform/fruit noise);
        # multi-word names need case-insensitive (filings use title case: "Super
        # Micro Computer"). Entities must be decoded first (&#160;/&#8217;), else
        # possessives and sentence boundaries match literal entity codes.
        text = _html.unescape(doc.decode("utf-8", "ignore"))
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        text_l = text.lower()
        needle, hay = (name, text) if " " not in name else (name_l, text_l)
        rel, snippet = None, None
        for m in re.finditer(re.escape(needle), hay):
            # the SENTENCE containing the mention (period-bounded, capped)
            s_lo = max(text_l.rfind(". ", 0, m.start()), text_l.rfind("? ", 0, m.start()),
                       text_l.rfind("! ", 0, m.start()), 0)
            s_hi = text_l.find(". ", m.end())
            s_hi = s_hi if s_hi != -1 else min(len(text), m.end() + _WINDOW)
            sent = text_l[s_lo:s_hi]
            if len(sent) > 600:
                sent = text_l[max(0, m.start() - _WINDOW): m.end() + _WINDOW]
            rel = next((r for r, rx in _LINK_RES if rx.search(sent)), None)
            if not rel:
                continue
            if _TECH_LIST_RE.search(sent):
                continue   # name in a vendor/platform list — not a relationship
            snippet = text[s_lo:s_hi].strip()[:260]
            break
        if not rel:
            continue
        seen.add(sym)
        out.append({"symbol": sym, "rel": rel,
                    "evidence": f"{src.get('form', '10-K')} {src.get('file_date', '')}: …{snippet}…",
                    "url": url})
    return out


# ---------------------------------------------------------------------------
# Polygon related-companies (factual peer set)
# ---------------------------------------------------------------------------

def polygon_peers(ticker: str, limit: int = 8) -> list[str]:
    """Related tickers per Polygon — competitors/peers, universe-filtered."""
    if not _POLYGON_KEY:
        return []
    raw = _get(f"https://api.polygon.io/v1/related-companies/{ticker.upper()}"
               f"?apiKey={_POLYGON_KEY}", timeout=15)
    if not raw:
        return []
    try:
        results = json.loads(raw).get("results", []) or []
    except ValueError:
        return []
    out = []
    for r in results:
        sym = (r.get("ticker") or "").upper()
        if sym and sym != ticker.upper() and in_universe(sym):
            out.append(sym)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# News-stated facts (persisted by the enrichment pipeline)
# ---------------------------------------------------------------------------

def news_relation_facts(symbol: str) -> list[dict]:
    """Relations about `symbol` stated in past news (with evidence URLs), from the
    accumulating relation_facts graph. rel is given from the ARTICLE's perspective
    (a SUPPLIES b, a COMPETES b, a PARTNERS b); the other side is the candidate."""
    facts = store.get_relation_facts(symbol)
    out = []
    for f in facts:
        other = f["to_sym"] if f["from_sym"] == symbol.upper() else f["from_sym"]
        if in_universe(other):
            out.append({"symbol": other, "rel": f["rel"], "evidence": f.get("evidence") or "",
                        "from_sym": f["from_sym"], "to_sym": f["to_sym"]})
    return out
