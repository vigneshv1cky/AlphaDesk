"""Ask a question of one SEC filing, answered ONLY from that filing's own
text, every claim backed by a verbatim quote.

Attribution here is stronger than the news screener's: the screener cites by
article INDEX and resolves that index back to a URL we already control (the
model's own idea of a URL is never trusted). A filing is one document, not a
numbered list, so there's no index to cite — instead the model is required to
quote verbatim, and every quote is checked as an actual substring of the
cached filing text before it's returned. A quote that doesn't verify is
dropped, not shown. The model can claim anything; only quotes that actually
appear in the SEC document survive.
"""

import hashlib
import logging

from alphadesk.ai.llm import LLMError, chat_json, wrap_data
from alphadesk.config import FILING_MAX_CHARS
from alphadesk.ingest import edgar
from alphadesk.ledger import store

log = logging.getLogger("alphadesk.filings")

_QA_SYSTEM = (
    "You answer questions about ONE SEC filing using ONLY the filing text "
    "provided. If the filing doesn't address the question, say so plainly — "
    "never guess or use outside knowledge.\n"
    "Every factual claim must be backed by a VERBATIM quote from the text — "
    "copy the exact wording, do not paraphrase into the quote field.\n"
    "Return ONLY JSON: {\"answer\": \"...\", "
    "\"quotes\": [\"verbatim snippet from the filing\", ...]}"
)


def _qhash(question: str) -> str:
    return hashlib.sha1(question.strip().lower().encode()).hexdigest()[:16]


def get_text(accession: str, url: str | None = None) -> str | None:
    """Cached filing text, fetching + extracting on first access. `url` is
    only needed on a cache miss (the caller usually already has it from
    recent_filings/get_filing_meta)."""
    cached = store.get_filing_text(accession)
    if cached is not None:
        return cached
    if not url:
        meta = store.get_filing_meta(accession)
        url = meta["url"] if meta else None
    if not url:
        return None
    text = edgar.fetch_filing_text(url, max_chars=FILING_MAX_CHARS)
    if text:
        store.save_filing_text(accession, text)
    return text


def _verify_quotes(quotes: list[str], text: str) -> list[dict]:
    """Keep only quotes that are an actual substring of the filing text
    (whitespace-normalized on both sides, since the model may not reproduce
    exact spacing) — this is the check that makes the citation real rather
    than the model's unverified say-so."""
    import re
    norm_text = re.sub(r"\s+", " ", text).lower()
    out = []
    for q in quotes:
        norm_q = re.sub(r"\s+", " ", q).strip().lower()
        if len(norm_q) >= 15 and norm_q in norm_text:
            out.append({"quote": q.strip()})
    return out


def ask(accession: str, question: str) -> dict | None:
    """Returns {answer, citations: [{quote}], verified_count, quoted_count}
    or None if the filing text isn't available (bad accession, fetch
    failure) or the model call fails — the caller shows 'try again', never a
    fabricated answer.

    Cached per (accession, question) — a re-ask of the same question is
    free; a rephrased one is a deliberate cache miss (the same amortization
    pattern desk/screener.py uses for its window-wide asks)."""
    text = get_text(accession)
    if not text:
        return None

    qh = _qhash(question)
    cached = store.get_filing_qa(accession, qh)
    if cached:
        return {"answer": cached["answer"], "citations": cached["citations"]}

    try:
        out = chat_json(
            _QA_SYSTEM,
            f"Question: {question}\n\nFiling text:\n" + wrap_data("filing", text),
            role="filing-qa", source="EDGAR", decision_id=accession,
            max_input_chars=FILING_MAX_CHARS + 2000, max_tokens=1024,
        )
    except LLMError as exc:
        log.warning("filing QA failed for %s: %s", accession, exc)
        return None

    answer = (out.get("answer") or "").strip()
    raw_quotes = [q for q in (out.get("quotes") or []) if isinstance(q, str)]
    citations = _verify_quotes(raw_quotes, text)
    if not answer:
        return None

    store.save_filing_qa(accession, qh, question, answer, citations, model="deepseek-chat")
    return {"answer": answer, "citations": citations}


def list_filings(symbol: str, refresh: bool = True) -> list[dict]:
    """A symbol's recent 10-K/10-Q/8-K filings. Refreshes from EDGAR on
    every call by default (cheap — one submissions JSON fetch) and persists
    into the filings table either way, so a later accession lookup
    (get_filing_meta) works even for a filing never re-listed since."""
    if refresh:
        fresh = edgar.recent_filings(symbol)
        if fresh:
            store.save_filings(fresh)
    return store.get_filings(symbol)
