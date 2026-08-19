# Data sources

Every upstream AlphaDesk touches, what it is used for, how it is collected, and
what its terms are. If you self-host this, these are **your** obligations — the
project ships the plumbing, not a licence to the data.

Nothing here is legal advice. Where a source's terms matter, the link is the
authority, not this table.

## Summary

| Source | Used for | Collection | Key | Cost | Terms to read |
|---|---|---|---|---|---|
| **SEC EDGAR** | filings list, filing text, Form 4 insider trades | official public JSON/HTML endpoints | none | free | [SEC access policy](https://www.sec.gov/os/webmaster-faq#developers) |
| **Alpaca** | quotes, intraday bars, movers, (optional) news | official REST API, `alpaca-py` | yes | free tier (IEX) | [Alpaca terms](https://alpaca.markets/terms) |
| **Polygon** | ticker-tagged news | official REST API, `polygon-api-client` | yes | paid | [Polygon terms](https://polygon.io/terms) |
| **Yahoo Finance** (via `yfinance`) | fundamentals, ownership, macro, sector, index tape, quote panel | **UNOFFICIAL — scrapes endpoints** | none | free | see the warning below |
| **Nasdaq** | earnings calendar | **UNOFFICIAL — undocumented JSON endpoint** | none | free | see the warning below |
| **Your LLM provider** | summarizing and answering | official API | yes | varies | your provider's terms |

## The two you should look at before deploying anything public

### Yahoo Finance via `yfinance`

`yfinance` is **not an official Yahoo API and is not endorsed by Yahoo.** It
reads endpoints that back Yahoo Finance's own website. That means:

- Yahoo's terms restrict use of their data, and the library's own documentation
  positions it for **personal and research use**.
- The endpoints are undocumented and can change or start rate-limiting without
  notice. This is a stability risk, not just a legal one.
- Redistributing this data — a public AlphaDesk instance other people read — is
  a materially different act from using it privately.

AlphaDesk leans on it hard: fundamentals, institutional ownership, macro,
sector performance, the index tape and the whole Equity Overview panel. If you
intend to run a public instance, replace it with a licensed feed or drop those
surfaces. Because prices are behind a `PriceProvider`, swapping is config plus
one implementation — see [providers.md](providers.md).

### Nasdaq earnings calendar

`ingest/earnings.py` reads an undocumented `api.nasdaq.com` JSON endpoint that
backs Nasdaq's public calendar page. Same shape of risk: no published terms
covering programmatic use, and it can change without warning.

## SEC EDGAR — free, but with a rule

SEC filing data is US-government public domain. Two conditions apply:

- **A descriptive User-Agent with real contact info is required** on every
  request. AlphaDesk reads `SEC_USER_AGENT`, which you MUST set; the default is
  a placeholder that identifies nobody. Requests without one get throttled or
  blocked.
- SEC asks for **no more than 10 requests/second**. `ingest/edgar.py` self-limits
  well under that.

Set it to your own address. It identifies your deployment's traffic, and a
shared or borrowed one gets someone else rate-limited for your requests.

## Licence: resolved (2026-08-18)

AlphaDesk previously depended on `openbb-core` / `openbb-sec`, which are
**AGPL-3.0-only**, while declaring MIT. AGPL §13 extends to software users
interact with over a network — exactly what this is — so the MIT declaration
was not consistent with the combined work.

Both are now removed. They existed for one feature, SEC Form 4 insider trades,
which `ingest/insider.py` reads straight from EDGAR instead: same data, one
fewer framework, and no copyleft obligation. MIT now governs cleanly.

## Dependency licences

Checked from installed package metadata:

| Package | Licence |
|---|---|
| alpaca-py | Apache-2.0 |
| yfinance | Apache-2.0 |
| polygon-api-client | MIT |
| beautifulsoup4 | MIT |
| pandas | BSD-3-Clause |
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| python-dotenv | BSD-3-Clause |
| mcp | MIT |

No AGPL or other copyleft dependency remains.

## What AlphaDesk stores

Locally, in SQLite (`ALPHADESK_DATA/ledger.db`) — never transmitted anywhere:

- news articles (headline, summary, url, source, symbols) and their enrichment
- SEC filing metadata and extracted text
- AI answers and their citations, cached
- the earnings calendar
- token spend per feature

Article and filing text is cached so the same content is not re-fetched or
re-summarized. If you make an instance public, you are redistributing that
cached upstream content — which is where the terms above start to bite.
