"""AlphaDesk entrypoint.

  python -m alphadesk.main dashboard      # the terminal (API + SPA)
  python -m alphadesk.main backfill --hours 168
  python -m alphadesk.main earnings       # refresh the calendar, show it

AlphaDesk is a CONSUMPTION terminal: it fetches, reads and presents market
information. It does not trade, hold positions, or score decisions — the
execution and measurement layers (entry booking, tiered exits, forward
grading vs SPY, backtests) were removed on 2026-08-18. Recover them from git
if that direction ever returns.
"""

import argparse
import asyncio
import logging
import sys


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # yfinance logs BRK.A/.B-style "possibly delisted" at ERROR for tickers it
    # can't price; the app handles missing prices, so silence the spam.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def _web_server():
    import os

    import uvicorn

    from alphadesk.app.dashboard import app as dashboard_app

    return uvicorn.Server(uvicorn.Config(
        dashboard_app,
        host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),  # VM sets 0.0.0.0
        port=int(os.environ.get("DASHBOARD_PORT", "8000")),
        log_level="warning",
    ))


async def _serve() -> None:
    """Two ingest loops and the web server. Everything else a page needs is
    fetched on the request path and cached."""

    async def _earnings_loop():
        from alphadesk.ingest import earnings
        loop = asyncio.get_running_loop()
        log = logging.getLogger("alphadesk.earnings")
        while True:
            try:
                await loop.run_in_executor(None, earnings.refresh_calendar)
                await loop.run_in_executor(None, earnings.arm_upcoming_reports)
                await loop.run_in_executor(None, earnings.arm_liquidity)
            except Exception as exc:
                log.error("earnings refresh error: %s", exc)
            from alphadesk.app import scheduler
            scheduler.beat()
            await asyncio.sleep(6 * 3600)   # 4x/day keeps upcoming + recent fresh

    async def _news_loop():
        """Background news ingest — poll Polygon since the last successful
        poll (first run: NEWS_LOOKBACK_HOURS), persist the raw articles, and
        enrich them (category/sentiment/relations).

        This is the ONLY unattended LLM call in the process, and it only
        labels text. Everything else the AI does happens on a request, when a
        human asks. /api/screener is a plain database read, so the window and
        its headlines survive a DeepSeek outage untouched."""
        from datetime import timedelta

        from alphadesk.config import NEWS_LOOKBACK_HOURS, NEWS_REFRESH_MINUTES, now_et
        from alphadesk.ingest import news
        loop = asyncio.get_running_loop()
        log = logging.getLogger("alphadesk.news")
        last_poll = now_et() - timedelta(hours=NEWS_LOOKBACK_HOURS)
        while True:
            try:
                since = last_poll
                last_poll = now_et()
                n = await loop.run_in_executor(None, news.poll, since)
                if n:
                    log.info("Ingested %d new articles", n)
            except Exception as exc:
                log.error("news ingest error: %s", exc)
                last_poll = since   # don't advance the window past a failed poll
            from alphadesk.app import scheduler
            scheduler.beat()
            await asyncio.sleep(NEWS_REFRESH_MINUTES * 60)

    await asyncio.gather(_earnings_loop(), _news_loop(), _web_server().serve())


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="alphadesk")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dashboard", help="run the terminal")
    p_back = sub.add_parser("backfill")
    p_back.add_argument("--hours", type=float, default=72)
    sub.add_parser("earnings", help="refresh the earnings calendar and show upcoming / recent")
    args = parser.parse_args()

    if args.cmd == "dashboard":
        import os
        log = logging.getLogger("alphadesk")
        log.info("Terminal on http://%s:%s",
                 os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
                 os.environ.get("DASHBOARD_PORT", "8000"))
        asyncio.run(_serve())
    elif args.cmd == "backfill":
        from alphadesk.ingest.earnings import refresh_calendar
        n = refresh_calendar(days_back=int(args.hours / 24) or 5)
        print(f"earnings calendar refreshed: {n} reporters")
    elif args.cmd == "earnings":
        from alphadesk.ingest import earnings
        from alphadesk.ledger import store
        print(f"calendar refreshed: {earnings.refresh_calendar()} rows")
        up = store.upcoming_earnings(days=7)
        print(f"\n=== reporting in the next 7 days ({len(up)}) ===")
        for e in up[:30]:
            print(f"  {e['report_date'][:16]}  {e['session'] or '?':3}  {e['symbol']:6}  est={e['eps_estimate']}")
        rec = store.recently_reported(days=3)
        print(f"\n=== reported in the last 3 days ({len(rec)}) ===")
        for e in rec:
            print(f"  {e['report_date'][:16]}  {e['symbol']:6}  est={e['eps_estimate']} act={e['eps_actual']}")
    sys.exit(0)


if __name__ == "__main__":
    main()
