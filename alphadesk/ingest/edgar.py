"""SEC EDGAR — free, no API key, no vendor. The document layer under
desk/filings.py.

Every endpoint here is a plain SEC-hosted JSON/HTML file, verified live
against real filings before this was written (see commit history — Apple's
CIK, its actual 10-Q shape, the full-text search param names). Three facts
that are easy to get wrong and will silently 403/empty-result you if you do:

  1. SEC requires a descriptive User-Agent with contact info on every request
     (`SEC_USER_AGENT`, see `_user_agent()`) — generic or missing gets
     throttled or blocked. There is no safe default; each deployment sets it.
  2. Full-text search filters by CIK via `ciks=` (zero-padded 10 digits), NOT
     `tickers=` — the latter is silently ignored and returns unfiltered
     results across every filer, which reads as "it worked" until you notice
     the company names in the results are wrong.
  3. Modern filings are inline XBRL: financial data tags are woven into the
     human-readable HTML, not a separate file. A naive tag-strip regex
     produces garbage near the document's XBRL-heavy front matter; this uses
     BeautifulSoup's get_text() instead, which handles it cleanly.
"""

import logging
import os
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger("alphadesk.edgar")

def _user_agent() -> str:
    """SEC requires a descriptive User-Agent with real contact info on every
    request, and throttles or blocks generic ones.

    This MUST be configured per deployment. It used to be one maintainer's
    address hardcoded — which meant every fork would have hammered EDGAR under
    his name and could have got him rate-limited for someone else's traffic.
    """
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if ua:
        return ua
    if not _user_agent._warned:                      # type: ignore[attr-defined]
        log.warning(
            "SEC_USER_AGENT is not set. SEC asks for a descriptive User-Agent with "
            "contact info (e.g. 'AlphaDesk (you@example.com)') and throttles requests "
            "without one — filings may fail or be slow until you set it.")
        _user_agent._warned = True                   # type: ignore[attr-defined]
    return "AlphaDesk (contact not configured; set SEC_USER_AGENT)"


_user_agent._warned = False                          # type: ignore[attr-defined]
_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
_FTS_URL = "https://efts.sec.gov/LATEST/search-index"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{doc}"

# SEC asks for <=10 req/s; a human clicking through the UI never gets close,
# but this floor keeps a burst (e.g. ingesting several forms for one symbol
# back to back) polite regardless.
_MIN_INTERVAL_S = 0.15
_last_request_t = 0.0

_ticker_cik_cache: dict[str, str] | None = None


def _get(url: str, timeout: float = 15.0) -> bytes:
    global _last_request_t
    wait = _MIN_INTERVAL_S - (time.monotonic() - _last_request_t)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    finally:
        _last_request_t = time.monotonic()


def _ticker_cik_map() -> dict[str, str]:
    """ticker -> zero-padded 10-digit CIK. One ~1MB file, cached for the
    process lifetime — SEC updates it a few times a day at most."""
    global _ticker_cik_cache
    if _ticker_cik_cache is not None:
        return _ticker_cik_cache
    import json
    try:
        data = json.loads(_get(_TICKER_MAP_URL))
        _ticker_cik_cache = {
            v["ticker"].upper(): f"{int(v['cik_str']):010d}" for v in data.values()
        }
    except Exception as exc:
        log.warning("EDGAR ticker map fetch failed: %s", exc)
        _ticker_cik_cache = {}
    return _ticker_cik_cache


def cik_for(symbol: str) -> str | None:
    return _ticker_cik_map().get(symbol.upper())


def recent_filings(symbol: str, forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
                   limit: int = 12) -> list[dict]:
    """This symbol's most recent filings of the given forms, newest first.
    Each: {accession, symbol, cik, form, filing_date, report_date,
    primary_doc, url}. Empty list (never raises) if the symbol has no CIK or
    the fetch fails — the caller degrades to 'no filings found', not a 500."""
    cik10 = cik_for(symbol)
    if not cik10:
        return []
    import json
    try:
        data = json.loads(_get(_SUBMISSIONS_URL.format(cik10=cik10)))
    except Exception as exc:
        log.warning("EDGAR submissions fetch failed for %s: %s", symbol, exc)
        return []
    recent = (data.get("filings") or {}).get("recent") or {}
    n = len(recent.get("form") or [])
    cik_int = str(int(cik10))   # archive URLs use the CIK unpadded
    out = []
    for i in range(n):
        form = recent["form"][i]
        if form not in forms:
            continue
        accession = recent["accessionNumber"][i]
        doc = recent["primaryDocument"][i]
        out.append({
            "accession": accession,
            "symbol": symbol.upper(),
            "cik": cik10,
            "form": form,
            "filing_date": recent["filingDate"][i],
            "report_date": recent.get("reportDate", [None] * n)[i],
            "primary_doc": doc,
            "url": _ARCHIVE_URL.format(
                cik=cik_int, accession_nodash=accession.replace("-", ""), doc=doc),
        })
        if len(out) >= limit:
            break
    return out


def full_text_search(query: str, symbol: str | None = None,
                     forms: tuple[str, ...] = ("10-K", "10-Q"), limit: int = 10) -> list[dict]:
    """Free-text search across EDGAR's indexed filings (2001-present).
    ciks= filters correctly; tickers= does not (see module docstring)."""
    import json
    from urllib.parse import urlencode
    params = {"q": query, "forms": ",".join(forms)}
    if symbol:
        cik10 = cik_for(symbol)
        if not cik10:
            return []
        params["ciks"] = cik10
    try:
        data = json.loads(_get(f"{_FTS_URL}?{urlencode(params)}"))
    except Exception as exc:
        log.warning("EDGAR full-text search failed (%r): %s", query, exc)
        return []
    out = []
    for h in (data.get("hits") or {}).get("hits", [])[:limit]:
        src = h.get("_source") or {}
        out.append({
            "id": h.get("_id"),
            "names": src.get("display_names"),
            "form": (src.get("root_forms") or [None])[0],
            "filed": src.get("file_date"),
        })
    return out


def fetch_filing_text(url: str, max_chars: int = 60_000) -> str | None:
    """Fetch one filing document and extract clean prose via BeautifulSoup
    (naive tag-stripping mangles inline-XBRL documents — see module
    docstring). Truncated to max_chars; a 10-K can run 200k+ chars and the
    caller (desk/filings.py) chunks/summarizes rather than feeding it whole
    to an LLM in one call."""
    try:
        raw = _get(url, timeout=30.0)
    except Exception as exc:
        log.warning("EDGAR document fetch failed (%s): %s", url, exc)
        return None
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)
    except Exception as exc:
        log.warning("EDGAR text extraction failed (%s): %s", url, exc)
        return None
    return text[:max_chars]
