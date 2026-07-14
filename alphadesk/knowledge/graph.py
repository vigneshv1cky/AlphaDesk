"""The knowledge graph (Neo4j) — AlphaDesk's world model.

Schema:
    (:Article {id, title, source, url, published_at})
        -[:MENTIONS {sentiment, label}]-> (:Company {symbol})
    (:Company)-[:SUPPLIES|COMPETES|PARTNERS {evidence_url, source, confidence,
        extracted_at}]->(:Company)

The ANALYSIS universe is open: any entity the news mentions becomes a node
(foreign/private companies included — they transmit ripples). Only the pick
universe constrains decision OUTPUTS, and that lives in llm.py's whitelist,
not here.

Edge provenance rules:
  • source="news_text"    — extracted from an article, evidence_url attached.
  • source="model_memory" — seeded from LLM parametric memory: QUARANTINED
    (confidence="low"); excluded from ripple activation until corroborated
    by a news-evidenced hop.

Availability: if Neo4j is down the graph no-ops (5-min retry pause) and
workflows proceed without graph briefs — the graph never blocks the pipeline.
"""

import logging
import os
import threading
import time
from typing import Any, Optional

log = logging.getLogger("alphadesk.graph")

_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
_USER = os.environ.get("NEO4J_USER", "neo4j")
_PASSWORD = os.environ.get("NEO4J_PASSWORD", "stocknews123")

_RETRY_AFTER_S = 300.0
_RELATION_TYPES = {"SUPPLIES", "COMPETES", "PARTNERS"}


class Graph:
    _default: Optional["Graph"] = None
    _default_lock = threading.Lock()

    def __init__(self, uri: str = _URI, user: str = _USER, password: str = _PASSWORD):
        self._uri = uri
        self._auth = (user, password)
        self._driver: Any = None
        self._lock = threading.Lock()
        self._disabled_until = 0.0
        self._schema_ready = False

    @classmethod
    def default(cls) -> "Graph":
        with cls._default_lock:
            if cls._default is None:
                cls._default = Graph()
            return cls._default

    # ------------------------------------------------------------------
    # Connection — never raises out of ingest/query paths
    # ------------------------------------------------------------------

    def _get_driver(self) -> Any:
        if time.time() < self._disabled_until:
            return None
        with self._lock:
            if self._driver is None:
                try:
                    from neo4j import GraphDatabase
                    self._driver = GraphDatabase.driver(
                        self._uri, auth=self._auth, connection_timeout=5.0
                    )
                    self._driver.verify_connectivity()
                except Exception as exc:
                    self._driver = None
                    self._disabled_until = time.time() + _RETRY_AFTER_S
                    log.warning("Neo4j unavailable (%s) — graph paused %.0fs", exc, _RETRY_AFTER_S)
                    return None
            if not self._schema_ready:
                try:
                    with self._driver.session() as s:
                        s.run("CREATE CONSTRAINT ad_article_id IF NOT EXISTS"
                              " FOR (a:Article) REQUIRE a.id IS UNIQUE")
                        s.run("CREATE CONSTRAINT ad_company_symbol IF NOT EXISTS"
                              " FOR (c:Company) REQUIRE c.symbol IS UNIQUE")
                        s.run("CREATE INDEX ad_article_published IF NOT EXISTS"
                              " FOR (a:Article) ON (a.published_at)")
                        s.run("CREATE CONSTRAINT ad_event_id IF NOT EXISTS"
                              " FOR (e:Event) REQUIRE e.id IS UNIQUE")
                        s.run("CREATE CONSTRAINT ad_theme_name IF NOT EXISTS"
                              " FOR (t:Theme) REQUIRE t.name IS UNIQUE")
                    self._schema_ready = True
                except Exception as exc:
                    log.warning("Neo4j schema setup failed: %s", exc)
        return self._driver

    def available(self) -> bool:
        return self._get_driver() is not None

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(self, articles: list[dict]) -> int:
        """Write enriched articles.

        Each article dict:
          {id, title, source, url, published_at(iso),
           mentions: [{symbol, sentiment, label}],
           relations: [{a, rel, b, evidence_url}]}       # rel ∈ SUPPLIES|COMPETES|PARTNERS
        """
        driver = self._get_driver()
        if driver is None or not articles:
            return 0

        mention_rows, relation_rows = [], []
        for art in articles:
            for m in art.get("mentions", []):
                mention_rows.append({
                    "id": art["id"], "title": art.get("title", "")[:300],
                    "source": art.get("source", ""), "url": art.get("url", ""),
                    "published_at": art["published_at"],
                    "symbol": m["symbol"].upper(),
                    "sentiment": float(m.get("sentiment", 0.0)),
                    "label": m.get("label", "neutral"),
                    "category": m.get("category", "UNCLASSIFIED"),
                })
            for r in art.get("relations", []):
                if r.get("rel") in _RELATION_TYPES and r.get("a") and r.get("b"):
                    relation_rows.append({
                        "a": r["a"].upper(), "b": r["b"].upper(), "rel": r["rel"],
                        "evidence_url": r.get("evidence_url", art.get("url", "")),
                    })
        try:
            with driver.session() as s:
                if mention_rows:
                    s.run(
                        """
                        UNWIND $rows AS row
                        MERGE (a:Article {id: row.id})
                          ON CREATE SET a.title = row.title, a.source = row.source,
                                        a.url = row.url,
                                        a.published_at = datetime(row.published_at)
                        MERGE (c:Company {symbol: row.symbol})
                        MERGE (a)-[m:MENTIONS]->(c)
                          SET m.sentiment = row.sentiment, m.label = row.label,
                              m.category = row.category
                        """,
                        rows=mention_rows,
                    )
                # typed relations: one query per type (relationship type can't be a parameter)
                for rel in _RELATION_TYPES:
                    rows = [r for r in relation_rows if r["rel"] == rel]
                    if rows:
                        s.run(
                            f"""
                            UNWIND $rows AS row
                            MERGE (x:Company {{symbol: row.a}})
                            MERGE (y:Company {{symbol: row.b}})
                            MERGE (x)-[e:{rel}]->(y)
                              ON CREATE SET e.source = 'news_text',
                                            e.confidence = 'evidenced',
                                            e.extracted_at = datetime()
                              SET e.evidence_url = row.evidence_url
                            """,
                            rows=rows,
                        )
            return len(mention_rows)
        except Exception as exc:
            log.warning("Graph ingest failed: %s", exc)
            return 0

    def ingest_world_events(self, events: list[dict]) -> int:
        """Write world events from the GDELT layer.

        (:Event {id, title, event_type, magnitude, url, occurred_at})
            -[:AFFECTS]->(:Theme {name})
        plus QUARANTINED exposure hypotheses:
        (:Company)-[:EXPOSED_TO {hypothesis: true, direction, chain,
            evidence_url}]->(:Theme)
        Hypothesis edges never activate ripple paths — they are research
        leads the committee must verify.
        """
        driver = self._get_driver()
        if driver is None or not events:
            return 0
        import hashlib
        event_rows, theme_rows, exposure_rows = [], [], []
        for ev in events:
            eid = hashlib.sha1(ev["url"].encode()).hexdigest()[:20]
            event_rows.append({
                "id": eid, "title": ev.get("title", "")[:250],
                "event_type": ev.get("event_type", "?"),
                "magnitude": ev.get("magnitude", "MINOR"),
                "url": ev["url"], "occurred_at": ev.get("published_at", ""),
            })
            for theme in ev.get("themes", []):
                theme_rows.append({"id": eid, "theme": theme[:60]})
                for exp in ev.get("exposures", []):
                    exposure_rows.append({
                        "symbol": (exp.get("symbol") or "").upper(),
                        "theme": theme[:60],
                        "direction": exp.get("direction", "LONG"),
                        "chain": exp.get("chain", "")[:250],
                        "evidence_url": ev["url"],
                    })
        try:
            with driver.session() as s:
                s.run(
                    """
                    UNWIND $rows AS row
                    MERGE (e:Event {id: row.id})
                      ON CREATE SET e.title = row.title, e.event_type = row.event_type,
                                    e.magnitude = row.magnitude, e.url = row.url,
                                    e.occurred_at = datetime(row.occurred_at)
                    """, rows=event_rows,
                )
                if theme_rows:
                    s.run(
                        """
                        UNWIND $rows AS row
                        MATCH (e:Event {id: row.id})
                        MERGE (t:Theme {name: row.theme})
                        MERGE (e)-[:AFFECTS]->(t)
                        """, rows=theme_rows,
                    )
                if exposure_rows:
                    s.run(
                        """
                        UNWIND $rows AS row
                        MERGE (c:Company {symbol: row.symbol})
                        MERGE (t:Theme {name: row.theme})
                        MERGE (c)-[x:EXPOSED_TO]->(t)
                          ON CREATE SET x.hypothesis = true, x.confidence = 'low'
                          SET x.direction = row.direction, x.chain = row.chain,
                              x.evidence_url = row.evidence_url
                        """, rows=exposure_rows,
                    )
            return len(event_rows)
        except Exception as exc:
            log.warning("World-event ingest failed: %s", exc)
            return 0

    def recent_world_events(self, hours: int = 48, limit: int = 30) -> list[dict]:
        """Recent world events with themes — deep-run seeds and brief context."""
        return self._read(
            """
            MATCH (e:Event)
            WHERE e.occurred_at >= datetime() - duration({hours: $hours})
            OPTIONAL MATCH (e)-[:AFFECTS]->(t:Theme)
            WITH e, collect(t.name) AS themes
            ORDER BY e.occurred_at DESC LIMIT $limit
            RETURN e.title AS title, e.event_type AS event_type,
                   e.magnitude AS magnitude, toString(e.occurred_at) AS occurred_at,
                   themes
            """, hours=hours, limit=limit,
        )

    def seed_relation(self, a: str, rel: str, b: str) -> None:
        """Memory-seeded edge — QUARANTINED until news-corroborated."""
        driver = self._get_driver()
        if driver is None or rel not in _RELATION_TYPES:
            return
        try:
            with driver.session() as s:
                s.run(
                    f"""
                    MERGE (x:Company {{symbol: $a}})
                    MERGE (y:Company {{symbol: $b}})
                    MERGE (x)-[e:{rel}]->(y)
                      ON CREATE SET e.source = 'model_memory', e.confidence = 'low',
                                    e.extracted_at = datetime()
                    """,
                    a=a.upper(), b=b.upper(),
                )
        except Exception as exc:
            log.warning("seed_relation failed: %s", exc)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def _read(self, cypher: str, **params: Any) -> list[dict]:
        driver = self._get_driver()
        if driver is None:
            return []
        try:
            with driver.session() as s:
                return [dict(r) for r in s.run(cypher, **params)]
        except Exception as exc:
            log.warning("Graph query failed: %s", exc)
            return []

    def neighborhood(self, symbol: str, hours: int = 72) -> dict:
        """Context block for briefs: typed neighbors (evidenced only) +
        co-mentioned companies + recent sentiment around the neighborhood."""
        sym = symbol.upper()
        typed = self._read(
            """
            MATCH (c:Company {symbol: $sym})-[e]-(peer:Company)
            WHERE type(e) IN ['SUPPLIES','COMPETES','PARTNERS']
              AND e.confidence <> 'low'
            RETURN type(e) AS rel, peer.symbol AS symbol,
                   startNode(e).symbol AS from_sym, e.evidence_url AS evidence
            LIMIT 20
            """, sym=sym,
        )
        comention = self._read(
            """
            MATCH (c:Company {symbol: $sym})<-[:MENTIONS]-(a:Article)-[m:MENTIONS]->(peer:Company)
            WHERE peer.symbol <> $sym
              AND a.published_at >= datetime() - duration({days: 30})
            RETURN peer.symbol AS symbol, count(a) AS shared,
                   round(avg(m.sentiment), 3) AS avg_sentiment
            ORDER BY shared DESC LIMIT 10
            """, sym=sym,
        )
        recent = self._read(
            """
            MATCH (a:Article)-[m:MENTIONS]->(c:Company {symbol: $sym})
            WHERE a.published_at >= datetime() - duration({hours: $hours})
            RETURN a.title AS title, a.source AS source,
                   toString(a.published_at) AS published_at, m.sentiment AS sentiment
            ORDER BY a.published_at DESC LIMIT 10
            """, sym=sym, hours=hours,
        )
        return {"symbol": sym, "typed_relations": typed,
                "co_mentioned": comention, "recent_articles": recent}

    def recent_shocks(self, hours: int = 24, limit: int = 40) -> list[dict]:
        """Companies ranked by news attention × sentiment intensity in the
        window (deep-run seed). A RANKING, not a filter — no sentiment cutoff;
        triage judges what deserves the committee's time."""
        return self._read(
            """
            MATCH (a:Article)-[m:MENTIONS]->(c:Company)
            WHERE a.published_at >= datetime() - duration({hours: $hours})
            WITH c.symbol AS symbol, count(a) AS articles,
                 round(avg(m.sentiment), 3) AS avg_sentiment
            RETURN symbol, articles, avg_sentiment,
                   round(articles * abs(avg_sentiment), 3) AS intensity
            ORDER BY intensity DESC, articles DESC LIMIT $limit
            """, hours=hours, limit=limit,
        )

    def summary(self) -> dict:
        rows = self._read(
            """
            MATCH (a:Article) WITH count(a) AS articles
            MATCH (c:Company) WITH articles, count(c) AS companies
            OPTIONAL MATCH ()-[m:MENTIONS]->()
            WITH articles, companies, count(m) AS mentions
            OPTIONAL MATCH ()-[r]->() WHERE type(r) IN ['SUPPLIES','COMPETES','PARTNERS']
            RETURN articles, companies, mentions, count(r) AS relations
            """
        )
        return rows[0] if rows else {"articles": 0, "companies": 0, "mentions": 0, "relations": 0}
