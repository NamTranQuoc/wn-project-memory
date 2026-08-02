from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import L1Reference
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


def _row_to_dict(row: L1Reference, *, score: float | None = None, full: bool = False) -> dict:
    payload = {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "project_path": row.project_path,
        "ref_key": row.ref_key,
        "title": sanitize_and_truncate(row.title),
        "content": row.content if full else sanitize_and_truncate(row.content),
        "is_policy": row.is_policy,
        "priority": row.priority,
        "source_id": str(row.source_id) if row.source_id else None,
        "raw_event_id": str(row.raw_event_id) if row.raw_event_id else None,
        "content_hash": row.content_hash,
        "source_hash": row.source_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if score is not None:
        payload["score"] = float(score)
    return payload


async def upsert_l1_reference(
    session: AsyncSession,
    project_path: str,
    *,
    ref_key: str,
    title: str,
    content: str,
    is_policy: bool = False,
    priority: int = 0,
    source_key: str | None = None,
    raw_event_id: uuid.UUID | str | None = None,
    source_hash: str | None = None,
    embedding: list[float] | None = None,
) -> dict:
    project = await get_or_create_project(session, project_path)

    source_id = await resolve_source_id(
        session, project_path, source_key, fallback_user_session=True
    )
    event_id = None
    if raw_event_id is not None:
        event_id = (
            raw_event_id if isinstance(raw_event_id, uuid.UUID) else uuid.UUID(str(raw_event_id))
        )

    content_hash = compute_content_hash(f"{ref_key}:{content}")
    hash_value = source_hash or compute_source_hash(content)
    if embedding is None:
        embedding = await embed_text(f"{title}\n{content}")

    result = await session.execute(
        select(L1Reference).where(
            L1Reference.project_id == project.id,
            L1Reference.ref_key == ref_key,
        )
    )
    existing = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if existing is None:
        row = L1Reference(
            project_id=project.id,
            project_path=project_path,
            ref_key=ref_key,
            title=title,
            content=content,
            is_policy=bool(is_policy),
            priority=int(priority),
            source_id=source_id,
            raw_event_id=event_id,
            content_hash=content_hash,
            source_hash=hash_value,
            embedding=embedding,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        out = _row_to_dict(row, full=True)
        out["action"] = "created"
        return out

    if existing.content_hash != content_hash or existing.source_hash != hash_value:
        existing.title = title
        existing.content = content
        existing.is_policy = bool(is_policy)
        existing.priority = int(priority)
        existing.source_id = source_id
        existing.raw_event_id = event_id if event_id is not None else existing.raw_event_id
        existing.content_hash = content_hash
        existing.source_hash = hash_value
        existing.embedding = embedding
        existing.updated_at = now
        await session.flush()
        out = _row_to_dict(existing, full=True)
        out["action"] = "overwritten"
        return out

    # content_hash only covers ref_key+content, not title/is_policy/priority — a
    # caller changing just one of those (e.g. flipping is_policy) with identical
    # content would otherwise be silently dropped here. Re-apply them explicitly.
    existing.title = title
    existing.is_policy = bool(is_policy)
    existing.priority = int(priority)
    existing.updated_at = now
    await session.flush()
    out = _row_to_dict(existing, full=True)
    out["action"] = "unchanged"
    return out


async def get_l1_reference(session: AsyncSession, project_path: str, ref_key: str) -> dict:
    result = await session.execute(
        select(L1Reference).where(
            L1Reference.project_path == project_path,
            L1Reference.ref_key == ref_key,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {"error": "not_found", "ref_key": ref_key}
    return _row_to_dict(row, full=True)


async def list_l1_references(
    session: AsyncSession,
    project_path: str,
    *,
    policy_only: bool = False,
) -> dict:
    stmt = select(L1Reference).where(L1Reference.project_path == project_path)
    if policy_only:
        stmt = stmt.where(L1Reference.is_policy.is_(True))
    stmt = stmt.order_by(L1Reference.priority.desc(), L1Reference.ref_key.asc())
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return {
        "project_path": project_path,
        "policy_only": policy_only,
        "count": len(rows),
        "references": [
            {
                "id": str(r.id),
                "ref_key": r.ref_key,
                "title": sanitize_and_truncate(r.title),
                "is_policy": r.is_policy,
                "priority": r.priority,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


async def _semantic_search(
    session: AsyncSession, project_path: str, query: str, limit: int
) -> list[dict]:
    embedding = await embed_text(query)
    distance = L1Reference.embedding.cosine_distance(embedding)
    stmt: Select = (
        select(L1Reference, distance.label("distance"))
        .where(L1Reference.project_path == project_path)
        .where(L1Reference.embedding.is_not(None))
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
    session: AsyncSession, project_path: str, query: str, limit: int
) -> list[dict]:
    similarity = func.similarity(L1Reference.content, query)
    stmt = (
        select(L1Reference, similarity.label("sim"))
        .where(L1Reference.project_path == project_path)
        .where(L1Reference.content.op("%")(query))
        .order_by(similarity.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = [_row_to_dict(row, score=float(sim or 0.0)) for row, sim in result.all()]
    if rows:
        return rows

    like_stmt = (
        select(L1Reference)
        .where(L1Reference.project_path == project_path)
        .where(L1Reference.content.ilike(f"%{query}%"))
        .limit(limit)
    )
    like_result = await session.execute(like_stmt)
    return [_row_to_dict(row, score=0.5) for row in like_result.scalars().all()]


async def search_l1_references(
    session: AsyncSession,
    project_path: str,
    query: str,
    *,
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
        semantic = await _semantic_search(session, project_path, query, capped)
        keyword = await _keyword_search(session, project_path, query, capped)
        merged: dict[str, dict] = {}
        for item in semantic + keyword:
            existing = merged.get(item["id"])
            if existing is None or item.get("score", 0) > existing.get("score", 0):
                merged[item["id"]] = item
        results = sorted(merged.values(), key=lambda x: x.get("score", 0), reverse=True)[:capped]

    return {
        "project_path": project_path,
        "query": query,
        "search_type": search_type,
        "limit": capped,
        "count": len(results),
        "results": results,
        "note": "content is truncated; call get_l1_reference for the full document",
    }


async def get_active_policies(session: AsyncSession, project_path: str) -> dict:
    stmt = (
        select(L1Reference)
        .where(
            L1Reference.project_path == project_path,
            L1Reference.is_policy.is_(True),
        )
        .order_by(L1Reference.priority.desc(), L1Reference.ref_key.asc())
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return {
        "project_path": project_path,
        "count": len(rows),
        "policies": [_row_to_dict(r, full=True) for r in rows],
    }
