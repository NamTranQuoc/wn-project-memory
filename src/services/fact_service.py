from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import FactKind, L3Fact
from src.services.embedding_service import embed_text
from src.services.hashing import compute_content_hash, compute_source_hash
from src.services.project_service import get_or_create_project
from src.services.sanitize import sanitize_and_truncate
from src.services.source_service import resolve_source_id


def _clamp_limit(limit: int | None) -> int:
    settings = get_settings()
    if limit is None:
        return settings.default_search_limit
    return max(1, min(int(limit), settings.max_search_limit))


def _row_to_dict(row: L3Fact, score: float | None = None) -> dict:
    payload = {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "project_path": row.project_path,
        "source_id": str(row.source_id),
        "raw_event_id": str(row.raw_event_id) if row.raw_event_id else None,
        "l3_entity_id": str(row.l3_entity_id) if row.l3_entity_id else None,
        "fact_key": row.fact_key,
        "kind": row.kind.value,
        "title": sanitize_and_truncate(row.title),
        "content": sanitize_and_truncate(row.content),
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "priority": row.priority,
        "status": row.status,
        "content_hash": row.content_hash,
        "source_hash": row.source_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if score is not None:
        payload["score"] = float(score)
    return payload


async def upsert_fact(
    session: AsyncSession,
    project_path: str,
    *,
    fact_key: str,
    kind: str,
    title: str,
    content: str,
    priority: int = 0,
    status: str | None = None,
    occurred_at: datetime | str | None = None,
    source_key: str | None = None,
    raw_event_id: uuid.UUID | str | None = None,
    l3_entity_id: uuid.UUID | str | None = None,
    source_hash: str | None = None,
    embedding: list[float] | None = None,
) -> dict:
    if not fact_key or not str(fact_key).strip():
        raise ValueError("fact_key is required")
    fact_key = str(fact_key).strip()

    project = await get_or_create_project(session, project_path)
    try:
        fact_kind = FactKind(kind)
    except ValueError as exc:
        raise ValueError(f"Invalid kind '{kind}'. Allowed: {[m.value for m in FactKind]}") from exc

    source_id = await resolve_source_id(
        session, project_path, source_key, fallback_user_session=True
    )
    if source_id is None:
        raise ValueError("source_id is required for facts")

    event_id = None
    if raw_event_id is not None:
        event_id = (
            raw_event_id if isinstance(raw_event_id, uuid.UUID) else uuid.UUID(str(raw_event_id))
        )
    entity_id = None
    if l3_entity_id is not None:
        entity_id = (
            l3_entity_id if isinstance(l3_entity_id, uuid.UUID) else uuid.UUID(str(l3_entity_id))
        )

    when = occurred_at
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if when is None:
        when = datetime.now(timezone.utc)

    content_hash = compute_content_hash(f"{fact_key}:{fact_kind.value}:{title}:{content}")
    hash_value = source_hash or compute_source_hash(content)
    if embedding is None:
        embedding = await embed_text(f"{title}\n{content}")

    result = await session.execute(
        select(L3Fact).where(
            L3Fact.project_id == project.id,
            L3Fact.fact_key == fact_key,
        )
    )
    existing = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if existing is None:
        row = L3Fact(
            project_id=project.id,
            project_path=project_path,
            source_id=source_id,
            raw_event_id=event_id,
            l3_entity_id=entity_id,
            fact_key=fact_key,
            kind=fact_kind,
            title=title,
            content=content,
            occurred_at=when,
            priority=int(priority),
            status=status,
            content_hash=content_hash,
            source_hash=hash_value,
            embedding=embedding,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        out = _row_to_dict(row)
        out["action"] = "created"
        return out

    if existing.content_hash != content_hash or existing.source_hash != hash_value:
        existing.kind = fact_kind
        existing.title = title
        existing.content = content
        existing.occurred_at = when
        existing.priority = int(priority)
        existing.status = status
        existing.source_id = source_id
        existing.raw_event_id = event_id if event_id is not None else existing.raw_event_id
        existing.l3_entity_id = entity_id if entity_id is not None else existing.l3_entity_id
        existing.content_hash = content_hash
        existing.source_hash = hash_value
        existing.embedding = embedding
        existing.updated_at = now
        await session.flush()
        out = _row_to_dict(existing)
        out["action"] = "overwritten"
        return out

    existing.source_id = source_id
    existing.updated_at = now
    await session.flush()
    out = _row_to_dict(existing)
    out["action"] = "unchanged"
    return out


async def get_fact(session: AsyncSession, project_path: str, fact_key: str) -> dict:
    result = await session.execute(
        select(L3Fact).where(
            L3Fact.project_path == project_path,
            L3Fact.fact_key == fact_key,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {"error": "not_found", "fact_key": fact_key}
    return _row_to_dict(row)


async def delete_fact(session: AsyncSession, project_path: str, fact_key: str) -> dict:
    result = await session.execute(
        select(L3Fact).where(
            L3Fact.project_path == project_path,
            L3Fact.fact_key == fact_key,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {"error": "not_found", "fact_key": fact_key}
    out = _row_to_dict(row)
    out["action"] = "deleted"
    await session.delete(row)
    await session.flush()
    return out


async def _semantic_search(
    session: AsyncSession,
    project_path: str,
    query: str,
    limit: int,
    kind: FactKind | None,
) -> list[dict]:
    embedding = await embed_text(query)
    distance = L3Fact.embedding.cosine_distance(embedding)
    stmt: Select = (
        select(L3Fact, distance.label("distance"))
        .where(L3Fact.project_path == project_path)
        .where(L3Fact.embedding.is_not(None))
    )
    if kind is not None:
        stmt = stmt.where(L3Fact.kind == kind)
    stmt = stmt.order_by(distance).limit(limit)
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
    kind: FactKind | None,
) -> list[dict]:
    similarity = func.similarity(L3Fact.content, query)
    stmt = (
        select(L3Fact, similarity.label("sim"))
        .where(L3Fact.project_path == project_path)
        .where(L3Fact.content.op("%")(query))
    )
    if kind is not None:
        stmt = stmt.where(L3Fact.kind == kind)
    stmt = stmt.order_by(similarity.desc()).limit(limit)
    result = await session.execute(stmt)
    rows = [_row_to_dict(row, score=float(sim or 0.0)) for row, sim in result.all()]
    if rows:
        return rows

    like_stmt = (
        select(L3Fact)
        .where(L3Fact.project_path == project_path)
        .where(L3Fact.content.ilike(f"%{query}%"))
    )
    if kind is not None:
        like_stmt = like_stmt.where(L3Fact.kind == kind)
    like_stmt = like_stmt.limit(limit)
    like_result = await session.execute(like_stmt)
    return [_row_to_dict(row, score=0.5) for row in like_result.scalars().all()]


async def search_facts(
    session: AsyncSession,
    project_path: str,
    query: str,
    *,
    search_type: str = "hybrid",
    limit: int | None = None,
    kind: str | None = None,
    order_by: str = "relevance",
) -> dict:
    capped = _clamp_limit(limit)
    fact_kind = None
    if kind:
        try:
            fact_kind = FactKind(kind)
        except ValueError as exc:
            raise ValueError(
                f"Invalid kind '{kind}'. Allowed: {[m.value for m in FactKind]}"
            ) from exc

    search_type = (search_type or "hybrid").lower()
    if search_type == "semantic":
        results = await _semantic_search(session, project_path, query, capped, fact_kind)
    elif search_type == "keyword":
        results = await _keyword_search(session, project_path, query, capped, fact_kind)
    else:
        semantic = await _semantic_search(session, project_path, query, capped, fact_kind)
        keyword = await _keyword_search(session, project_path, query, capped, fact_kind)
        merged: dict[str, dict] = {}
        for item in semantic + keyword:
            existing = merged.get(item["id"])
            if existing is None or item.get("score", 0) > existing.get("score", 0):
                merged[item["id"]] = item
        results = sorted(merged.values(), key=lambda x: x.get("score", 0), reverse=True)[:capped]

    if order_by == "priority":
        results = sorted(results, key=lambda x: x.get("priority", 0), reverse=True)
    elif order_by == "occurred_at":
        results = sorted(results, key=lambda x: x.get("occurred_at") or "", reverse=True)

    return {
        "project_path": project_path,
        "query": query,
        "search_type": search_type,
        "kind": kind,
        "order_by": order_by,
        "limit": capped,
        "count": len(results),
        "results": results,
    }


async def list_facts(
    session: AsyncSession,
    project_path: str,
    *,
    kind: str | None = None,
    order_by: str = "occurred_at",
    limit: int | None = None,
) -> dict:
    capped = _clamp_limit(limit)
    stmt = select(L3Fact).where(L3Fact.project_path == project_path)
    if kind:
        stmt = stmt.where(L3Fact.kind == FactKind(kind))
    if order_by == "priority":
        stmt = stmt.order_by(L3Fact.priority.desc(), L3Fact.occurred_at.desc())
    else:
        stmt = stmt.order_by(L3Fact.occurred_at.desc())
    stmt = stmt.limit(capped)
    result = await session.execute(stmt)
    rows = [_row_to_dict(r) for r in result.scalars().all()]
    return {
        "project_path": project_path,
        "kind": kind,
        "order_by": order_by,
        "count": len(rows),
        "facts": rows,
    }
