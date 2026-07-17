"""Specialist brief subagents — ephemeral, parallel, haiku.

Three one-shot workers per candidate: technical (price structure), news
(what was actually said), graph (neighborhood + priced-check evidence).
Each returns a compact evidence block the committee argues from.
"""

import json
import logging

from alphadesk.llm import LLMError, call_role, wrap_data

log = logging.getLogger("alphadesk.briefs")

_BRIEF_SCHEMA = {
    "summary": {"type": str, "maxlen": 600},
    "key_facts": {"type": list, "maxitems": 5, "items": {"fact": {"type": str, "maxlen": 200}}},
}


def _brief(kind: str, instructions: str, payload: str, decision_id: str | None) -> dict:
    try:
        out = call_role(
            "brief",
            f"You are the {kind} research specialist on a stock research desk. {instructions} "
            'Be factual and terse — no speculation. Return ONLY JSON: '
            '{"summary": "<4 sentences max>", "key_facts": [{"fact": "..."}]}',
            payload,
            schema=_BRIEF_SCHEMA,
            decision_id=decision_id,
        )
        out["kind"] = kind
        return out
    except LLMError as exc:
        log.warning("%s brief failed: %s", kind, exc)
        return {"kind": kind, "summary": f"({kind} brief unavailable)", "key_facts": []}


def technical_brief(symbol: str, price_ctx: dict | None, decision_id: str | None = None) -> dict:
    if not price_ctx:
        return {"kind": "technical", "summary": "(no price data)", "key_facts": []}
    return _brief(
        "technical",
        "Describe the price structure: trend, where price sits vs its range, "
        "whether recent action looks extended or quiet, and liquidity.",
        "Price data:\n" + wrap_data("prices", json.dumps(price_ctx)),
        decision_id,
    )


def news_brief(symbol: str, articles: list[dict], decision_id: str | None = None) -> dict:
    lines = "\n".join(
        f"- [{a.get('published_at','')[:16]}] ({a.get('source','')}) {a.get('title','')}"
        + (f" — {a.get('summary','')[:150]}" if a.get("summary") else "")
        + f" | sentiment={a['mentions'][0]['sentiment'] if a.get('mentions') else '?'}"
        for a in articles[:10]
    )
    return _brief(
        "news",
        f"Summarize what has actually been reported about {symbol}: the concrete "
        "catalyst(s), how fresh they are, whether sources corroborate, and what is "
        "claimed vs merely speculated.",
        f"Recent articles for {symbol}:\n" + wrap_data("articles", lines or "none"),
        decision_id,
    )


def market_brief(symbol: str, price_ctx: dict | None, fundamentals: dict | None,
                 articles: list[dict], decision_id: str | None = None) -> dict:
    """One call covering the three code-fact dimensions that used to be three
    briefs: technicals, valuation, and the priced-in / still-developing read."""
    payload = {
        "price": price_ctx or "none",
        "fundamentals": fundamentals or "none",
        "catalyst_timestamps": [a.get("published_at", "")[:16] for a in articles[:6]],
    }
    return _brief(
        "market",
        f"Give the market backdrop for {symbol} in three tight parts, using ONLY "
        "the numbers provided (invent nothing). (1) TECHNICALS: trend, where price "
        "sits in its range, extended vs quiet, liquidity. (2) VALUATION: cheap or "
        "rich, profitable/growing, whether the valuation leaves room for the "
        "catalyst or is priced for perfection. (3) PRICED-IN & LEGS: compare "
        "catalyst timing to the move — already moved hard (fade risk) vs barely "
        "moved (repricing may be ahead); and is this a spent POINT event or a "
        "STILL-DEVELOPING story with multi-day drift left.",
        "Data:\n" + wrap_data("market", json.dumps(payload, default=str)),
        decision_id,
    )


def graph_brief(symbol: str, neighborhood: dict, neighbor_moves: dict[str, float],
                decision_id: str | None = None) -> dict:
    payload = {
        "typed_relations": neighborhood.get("typed_relations", []),
        "co_mentioned_30d": neighborhood.get("co_mentioned", []),
        "recent_articles": neighborhood.get("recent_articles", [])[:6],
        "neighbor_5d_moves_pct": neighbor_moves,
    }
    return _brief(
        "graph",
        f"Describe {symbol}'s relationship neighborhood: which connected companies "
        "had significant news, the evidence for each connection, and — critically — "
        "whether connected-company moves suggest any spillover is ALREADY PRICED "
        "(compare event direction vs the neighbor 5-day moves provided).",
        "Neighborhood data:\n" + wrap_data("graph", json.dumps(payload, default=str)),
        decision_id,
    )
