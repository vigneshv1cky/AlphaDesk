"""SEC Form 4 insider trades, read straight from EDGAR.

Replaces an `openbb-sec` dependency that existed for this one feature. Two
reasons to drop it: openbb-core/openbb-sec are AGPL-3.0-only while this project
is MIT (see docs/data-sources.md), and pulling a provider framework in to parse
one well-defined XML schema was never a good trade — `ingest/edgar.py` already
talks to SEC directly and this is ~120 lines against that.

Two things about Form 4 that are easy to get wrong:

  1. `primaryDocument` in the submissions JSON points at the XSL-RENDERED view
     (`xslF345X06/form4.xml`), which is HTML, not the data. The raw XML sits in
     the same folder under the bare filename — strip the `xsl*/` prefix.
  2. A Form 4 carries BOTH non-derivative (ordinary shares) and derivative
     (options, RSUs) tables. Only the first answers "did an insider buy or sell
     stock"; derivative rows are grant and exercise bookkeeping and would swamp
     the signal.

Institutional ownership (13F) is deliberately NOT here — Form 13F is filed BY a
manager listing THEIR holdings, so it cannot answer "who holds this stock".
That stays on yfinance (`prices.institutional_ownership`).
"""

from __future__ import annotations

import logging
import threading
import time
import xml.etree.ElementTree as ET

from alphadesk.config import OWNERSHIP_TTL_S

log = logging.getLogger("alphadesk.insider")

_cache_lock = threading.Lock()
_insider_cache: dict[str, tuple[float, list[dict] | None]] = {}

# Transaction codes worth naming. P/S are open-market buys and sells — the ones
# a "did an insider buy" question actually means. A and F are compensation
# mechanics that look like trades but are not discretionary.
_CODE_MEANING = {
    "P": "open-market purchase",
    "S": "open-market sale",
    "A": "grant or award",
    "F": "shares withheld for tax",
    "M": "option exercise",
    "G": "gift",
    "D": "disposition to issuer",
    "C": "conversion",
    "X": "option exercise (in-the-money)",
}


def _text(node, path: str) -> str:
    if node is None:
        return ""
    return (node.findtext(path) or "").strip()


def _num(node, path: str) -> float | None:
    raw = _text(node, path)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_form4(raw: bytes, filing_date: str, url: str) -> list[dict]:
    """One Form 4 XML -> its non-derivative transactions."""
    root = ET.fromstring(raw)
    issuer = _text(root, "issuer/issuerName")
    symbol = _text(root, "issuer/issuerTradingSymbol").upper()
    owner = _text(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    is_dir = _text(rel, "isDirector") in ("1", "true")
    is_off = _text(rel, "isOfficer") in ("1", "true")
    is_ten = _text(rel, "isTenPercentOwner") in ("1", "true")
    title = _text(rel, "officerTitle")

    out = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(tx, "transactionCoding/transactionCode")
        shares = _num(tx, "transactionAmounts/transactionShares/value")
        price = _num(tx, "transactionAmounts/transactionPricePerShare/value")
        acq_disp = _text(tx, "transactionAmounts/transactionAcquiredDisposedCode/value")
        out.append({
            "symbol": symbol,
            "company_name": issuer,
            "filing_date": filing_date,
            "transaction_date": _text(tx, "transactionDate/value"),
            "owner_name": owner,
            "owner_title": title or None,
            "director": is_dir,
            "officer": is_off,
            "ten_percent_owner": is_ten,
            "security_type": _text(tx, "securityTitle/value"),
            "transaction_code": code,
            "transaction_type": _CODE_MEANING.get(code, code),
            # A = acquired, D = disposed. Spelled out because a one-letter code
            # in an AI answer is not something a reader can check.
            "acquisition_or_disposition": (
                "acquired" if acq_disp == "A" else "disposed" if acq_disp == "D" else acq_disp
            ),
            "securities_transacted": shares,
            "transaction_price": price,
            "transaction_value": round(shares * price, 2) if (shares and price) else None,
            "securities_owned_after": _num(
                tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
            "filing_url": url,
        })
    return out


def get_insider_trades(symbol: str, limit: int = 20) -> list[dict] | None:
    """Recent Form 4 activity for a symbol, newest first.

    Best-effort and cached: returns None on any failure (no CIK, SEC outage,
    unparseable filing) so one dead source degrades a single research section
    rather than the request.
    """
    sym = symbol.upper()
    with _cache_lock:
        hit = _insider_cache.get(sym)
        if hit and time.time() - hit[0] < OWNERSHIP_TTL_S:
            return hit[1]

    out: list[dict] | None = None
    try:
        import json

        from alphadesk.ingest import edgar
        cik10 = edgar.cik_for(sym)
        if not cik10:
            raise ValueError(f"no CIK for {sym}")
        cik_int = str(int(cik10))
        data = json.loads(edgar._get(edgar._SUBMISSIONS_URL.format(cik10=cik10)))
        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []

        rows: list[dict] = []
        # Each Form 4 is its own HTTP fetch, so cap how many we open rather than
        # walking hundreds of filings for one research section.
        for i, form in enumerate(forms):
            if form != "4":
                continue
            acc = recent["accessionNumber"][i]
            # The XSL view directory is a rendering, not the data — see module
            # docstring. The bare filename in the same folder is the XML.
            doc = (recent["primaryDocument"][i] or "").split("/")[-1]
            if not doc.endswith(".xml"):
                continue
            url = edgar._ARCHIVE_URL.format(
                cik=cik_int, accession_nodash=acc.replace("-", ""), doc=doc)
            try:
                rows.extend(_parse_form4(edgar._get(url), recent["filingDate"][i], url))
            except Exception as exc:
                log.debug("form 4 parse failed (%s): %s", url, exc)
            if len(rows) >= limit:
                break
        out = rows[:limit] or None
    except Exception as exc:
        log.debug("insider trades failed %s: %s", sym, exc)

    with _cache_lock:
        _insider_cache[sym] = (time.time(), out)
    return out
