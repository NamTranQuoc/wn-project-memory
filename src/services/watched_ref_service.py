from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import L3WatchedRef, WatchedRefDisposition, WatchedRefType
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


def _row_to_dict(row: L3WatchedRef) -> dict:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "project_path": row.project_path,
        "source_id": str(row.source_id) if row.source_id else None,
        "raw_event_id": str(row.raw_event_id) if row.raw_event_id else None,
        "l3_entity_id": str(row.l3_entity_id) if row.l3_entity_id else None,
        "ref_type": row.ref_type.value,
        "ref_value": row.ref_value,
        "why": sanitize_and_truncate(row.why),
        "status_note": sanitize_and_truncate(row.status_note),
        "disposition": row.disposition.value,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "content_hash": row.content_hash,
        "source_hash": row.source_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def upsert_watched_ref(
    session: AsyncSession,
    project_path: str,
    *,
    ref_type: str,
    ref_value: str,
    why: str | None = None,
    status_note: str | None = None,
    disposition: str = "queued",
    source_key: str | None = None,
    raw_event_id: uuid.UUID | str | None = None,
    l3_entity_id: uuid.UUID | str | None = None,
    source_hash: str | None = None,
    embedding: list[float] | None = None,
) -> dict:
    project = await get_or_create_project(session, project_path)
    try:
        rtype = WatchedRefType(ref_type)
    except ValueError as exc:
        raise ValueError(
            f"Invalid ref_type '{ref_type}'. Allowed: {[m.value for m in WatchedRefType]}"
        ) from exc
    try:
        disp = WatchedRefDisposition(disposition)
    except ValueError as exc:
        raise ValueError(
            f"Invalid disposition '{disposition}'. "
            f"Allowed: {[m.value for m in WatchedRefDisposition]}"
        ) from exc

    source_id = await resolve_source_id(
        session, project_path, source_key, fallback_user_session=True
    )
    if source_id is None:
        raise ValueError("source_id is required for watched refs")
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

    content_hash = compute_content_hash(
        f"{rtype.value}:{ref_value}:{why or ''}:{status_note or ''}:{disp.value}"
    )
    hash_value = source_hash or compute_source_hash(why or ref_value)
    if embedding is None and why:
        embedding = await embed_text(why)

    result = await session.execute(
        select(L3WatchedRef).where(
            L3WatchedRef.project_id == project.id,
            L3WatchedRef.ref_type == rtype,
            L3WatchedRef.ref_value == ref_value,
        )
    )
    existing = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if existing is None:
        row = L3WatchedRef(
            project_id=project.id,
            project_path=project_path,
            source_id=source_id,
            raw_event_id=event_id,
            l3_entity_id=entity_id,
            ref_type=rtype,
            ref_value=ref_value,
            why=why,
            status_note=status_note,
            disposition=disp,
            first_seen_at=now,
            last_seen_at=now,
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

    next_why = why if why is not None else existing.why
    next_status_note = status_note if status_note is not None else existing.status_note
    next_hash = compute_content_hash(
        f"{rtype.value}:{ref_value}:{next_why or ''}:{next_status_note or ''}:{disp.value}"
    )
    if existing.content_hash != next_hash or existing.source_hash != hash_value:
        existing.why = next_why
        existing.status_note = next_status_note
        existing.disposition = disp
        existing.last_seen_at = now
        existing.source_id = source_id
        existing.raw_event_id = event_id if event_id is not None else existing.raw_event_id
        existing.l3_entity_id = entity_id if entity_id is not None else existing.l3_entity_id
        existing.content_hash = next_hash
        existing.source_hash = hash_value
        if embedding is not None:
            existing.embedding = embedding
        existing.updated_at = now
        await session.flush()
        out = _row_to_dict(existing)
        out["action"] = "overwritten"
        return out

    existing.last_seen_at = now
    existing.source_id = source_id
    existing.updated_at = now
    await session.flush()
    out = _row_to_dict(existing)
    out["action"] = "unchanged"
    return out


async def list_watched_refs(
    session: AsyncSession,
    project_path: str,
    *,
    disposition: str | None = None,
    limit: int | None = None,
) -> dict:
    capped = _clamp_limit(limit)
    stmt = select(L3WatchedRef).where(L3WatchedRef.project_path == project_path)
    if disposition:
        stmt = stmt.where(L3WatchedRef.disposition == WatchedRefDisposition(disposition))
    stmt = stmt.order_by(L3WatchedRef.last_seen_at.desc()).limit(capped)
    result = await session.execute(stmt)
    rows = [_row_to_dict(r) for r in result.scalars().all()]
    return {
        "project_path": project_path,
        "disposition": disposition,
        "count": len(rows),
        "watched_refs": rows,
    }
