from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import L3DistilledKnowledge
from src.services.embedding_service import embed_text
from src.services.sanitize import sanitize_and_truncate


def _clamp_limit(limit: int | None) -> int:
    settings = get_settings()
    if limit is None:
        return settings.default_search_limit
    return max(1, min(int(limit), settings.max_search_limit))


def _row_to_dict(row: L3DistilledKnowledge, score: float | None = None) -> dict:
    payload = {
        "id": str(row.id),
        "project_path": row.project_path,
        "entity_key": row.entity_key,
        "content": sanitize_and_truncate(row.content),
        "content_hash": row.content_hash,
        "source_hash": row.source_hash,
        "raw_event_id": str(row.raw_event_id) if row.raw_event_id else None,
        "last_verified_at": (
            row.last_verified_at.isoformat() if row.last_verified_at else None
        ),
    }
    if score is not None:
        payload["score"] = float(score)
    return payload


async def _semantic_search(
    session: AsyncSession,
    project_path: str,
    query: str,
    limit: int,
) -> list[dict]:
    embedding = await embed_text(query)
    distance = L3DistilledKnowledge.embedding.cosine_distance(embedding)
    stmt: Select = (
        select(L3DistilledKnowledge, distance.label("distance"))
        .where(L3DistilledKnowledge.project_path == project_path)
        .where(L3DistilledKnowledge.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = []
    for row, dist in result.all():
        score = 1.0 - float(dist) if dist is not None else 0.0
        rows.append(_row_to_dict(row, score=score))
    return rows


async def _keyword_search(
    session: AsyncSession,
    project_path: str,
    query: str,
    limit: int,
) -> list[dict]:
    similarity = func.similarity(L3DistilledKnowledge.content, query)
    stmt = (
        select(L3DistilledKnowledge, similarity.label("sim"))
        .where(L3DistilledKnowledge.project_path == project_path)
        .where(L3DistilledKnowledge.content.op("%")(query))
        .order_by(similarity.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    # Fallback if trigram operator yields nothing: ILIKE contains
    rows = [(_row_to_dict(row, score=float(sim or 0.0))) for row, sim in result.all()]
    if rows:
        return rows

    like_stmt = (
        select(L3DistilledKnowledge)
        .where(L3DistilledKnowledge.project_path == project_path)
        .where(L3DistilledKnowledge.content.ilike(f"%{query}%"))
        .limit(limit)
    )
    like_result = await session.execute(like_stmt)
    return [_row_to_dict(row, score=0.5) for row in like_result.scalars().all()]


async def search_memory(
    session: AsyncSession,
    project_path: str,
    query: str,
    search_type: str = "hybrid",
    limit: int | None = None,
) -> dict:
    capped = _clamp_limit(limit)
    search_type = (search_type or "hybrid").lower()

    if search_type == "semantic":
        results = await _semantic_search(session, project_path, query, capped)
    elif search_type == "keyword":
        results = await _keyword_search(session, project_path, query, capped)
    else:
        # Hybrid: merge by id, prefer higher score
        semantic = await _semantic_search(session, project_path, query, capped)
        keyword = await _keyword_search(session, project_path, query, capped)
        merged: dict[str, dict] = {}
        for item in semantic + keyword:
            existing = merged.get(item["id"])
            if existing is None or item.get("score", 0) > existing.get("score", 0):
                merged[item["id"]] = item
        results = sorted(
            merged.values(), key=lambda x: x.get("score", 0), reverse=True
        )[:capped]

    return {
        "project_path": project_path,
        "query": query,
        "search_type": search_type,
        "limit": capped,
        "count": len(results),
        "results": results,
    }
