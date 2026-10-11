"""Search over L2 namespaces and L3 facts.

Keyword search (Postgres full-text, see text_search.py) is always on. Semantic search exists only when
SEMANTIC_SEARCH_ENABLED=true; with it off this module makes no network call.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Fact, FactKind, Namespace
from src.services.common import iso, require_text, search_limit
from src.services.embedding import embed_text, semantic_enabled
from src.services.l1_service import require_project
from src.services.text_search import (
    all_terms,
    any_term,
    namespace_matches,
    rank,
    search_terms,
    snippet,
)


def _hit(fact: Fact, namespace: str, score: float, passage: str | None = None) -> dict:
    out = {
        "fact_key": fact.fact_key,
        "namespace": namespace,
        "kind": fact.kind.value,
        "status": fact.status,
        "summary": fact.summary,
        "revision": fact.revision,
        "updated_at": iso(fact.updated_at),
        "score": round(float(score or 0.0), 4),
    }
    if passage:
        out["snippet"] = passage
    return out


async def search_facts(
    session: AsyncSession,
    project_path: str,
    query: str,
    *,
    kind: str | None = None,
    limit: int | None = None,
) -> dict:
    """Namespaces first, then facts. Returns keys and summaries; read content with get_fact."""
    project = await require_project(session, project_path)
    q = require_text(query, field="query")
    terms = search_terms(q)
    capped = search_limit(limit)

    ns_rows = (
        await session.execute(
            select(Namespace, rank(Namespace.search_tsv, terms).label("score"))
            .where(Namespace.project_id == project.id)
            .where(namespace_matches(terms))
            .order_by(Namespace.search_tsv.op("@@")(all_terms(terms)).desc(),
                      text("score DESC"))
            .limit(capped)
        )
    ).all()

    fact_score = rank(Fact.search_tsv, terms)
    stmt = (
        select(Fact, Namespace.namespace, fact_score.label("score"),
               snippet(Fact.content, terms).label("snippet"))
        .join(Namespace, Namespace.id == Fact.namespace_id)
        .where(Fact.project_id == project.id)
        .where(or_(Fact.search_tsv.op("@@")(any_term(terms)), Fact.title.op("%")(q)))
    )
    if kind:
        stmt = stmt.where(Fact.kind == FactKind(kind))
    stmt = stmt.order_by(
        Fact.search_tsv.op("@@")(all_terms(terms)).desc(),
        text("score DESC"),
        func.similarity(Fact.title, q).desc(),
    )
    fact_rows = (await session.execute(stmt.limit(capped))).all()

    return {
        "query": q,
        "namespaces": [
            {"namespace": ns.namespace, "key_template": ns.key_template, "summary": ns.summary,
             "score": round(float(score or 0.0), 4)}
            for ns, score in ns_rows
        ],
        "facts": [_hit(f, ns, score, passage) for f, ns, score, passage in fact_rows],
        "limit": capped,
        "next": "the snippet often answers; get_fact(fact_key) for the full value",
    }


async def semantic_search(
    session: AsyncSession,
    project_path: str,
    query: str,
    *,
    kind: str | None = None,
    limit: int | None = None,
) -> dict:
    if not semantic_enabled():
        raise ValueError("semantic search is off (SEMANTIC_SEARCH_ENABLED=false)")
    project = await require_project(session, project_path)
    vector = await embed_text(require_text(query, field="query"))
    capped = search_limit(limit)
    distance = Fact.embedding.cosine_distance(vector)
    stmt = (
        select(Fact, Namespace.namespace, distance.label("distance"))
        .join(Namespace, Namespace.id == Fact.namespace_id)
        .where(Fact.project_id == project.id, Fact.embedding.is_not(None))
    )
    if kind:
        stmt = stmt.where(Fact.kind == FactKind(kind))
    rows = (await session.execute(stmt.order_by(distance).limit(capped))).all()
    return {
        "query": query,
        "facts": [_hit(f, ns, 1.0 - float(d)) for f, ns, d in rows],
        "limit": capped,
    }
