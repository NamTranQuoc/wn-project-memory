from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import L3Task, TaskStatus
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


def _row_to_dict(row: L3Task) -> dict:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "project_path": row.project_path,
        "source_id": str(row.source_id) if row.source_id else None,
        "raw_event_id": str(row.raw_event_id) if row.raw_event_id else None,
        "l3_entity_id": str(row.l3_entity_id) if row.l3_entity_id else None,
        "task_key": row.task_key,
        "title": sanitize_and_truncate(row.title),
        "content": sanitize_and_truncate(row.content),
        "status": row.status.value,
        "priority": row.priority,
        "waiting_on": row.waiting_on,
        "since_at": row.since_at.isoformat() if row.since_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "content_hash": row.content_hash,
        "source_hash": row.source_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def upsert_task(
    session: AsyncSession,
    project_path: str,
    *,
    task_key: str,
    title: str,
    content: str,
    status: str = "open",
    priority: int = 0,
    waiting_on: str | None = None,
    since_at: datetime | str | None = None,
    source_key: str | None = None,
    raw_event_id: uuid.UUID | str | None = None,
    l3_entity_id: uuid.UUID | str | None = None,
    source_hash: str | None = None,
    embedding: list[float] | None = None,
) -> dict:
    project = await get_or_create_project(session, project_path)
    try:
        task_status = TaskStatus(status)
    except ValueError as exc:
        raise ValueError(
            f"Invalid status '{status}'. Allowed: {[m.value for m in TaskStatus]}"
        ) from exc

    source_id = await resolve_source_id(
        session, project_path, source_key, fallback_user_session=True
    )
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

    when = since_at
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if when is None:
        when = datetime.now(timezone.utc)

    content_hash = compute_content_hash(f"{task_key}:{title}:{content}:{status}")
    hash_value = source_hash or compute_source_hash(content)
    if embedding is None:
        embedding = await embed_text(f"{title}\n{content}")

    result = await session.execute(
        select(L3Task).where(
            L3Task.project_id == project.id,
            L3Task.task_key == task_key,
        )
    )
    existing = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if existing is None:
        row = L3Task(
            project_id=project.id,
            project_path=project_path,
            source_id=source_id,
            raw_event_id=event_id,
            l3_entity_id=entity_id,
            task_key=task_key,
            title=title,
            content=content,
            status=task_status,
            priority=int(priority),
            waiting_on=waiting_on,
            since_at=when,
            closed_at=now if task_status == TaskStatus.closed else None,
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

    existing.title = title
    existing.content = content
    existing.status = task_status
    existing.priority = int(priority)
    existing.waiting_on = waiting_on
    existing.since_at = when
    existing.source_id = source_id
    existing.raw_event_id = event_id if event_id is not None else existing.raw_event_id
    existing.l3_entity_id = entity_id if entity_id is not None else existing.l3_entity_id
    existing.content_hash = content_hash
    existing.source_hash = hash_value
    existing.embedding = embedding
    if task_status == TaskStatus.closed and existing.closed_at is None:
        existing.closed_at = now
    if task_status != TaskStatus.closed:
        existing.closed_at = None
    existing.updated_at = now
    await session.flush()
    out = _row_to_dict(existing)
    out["action"] = "updated"
    return out


async def close_task(
    session: AsyncSession,
    project_path: str,
    task_key: str,
    *,
    content: str | None = None,
) -> dict:
    result = await session.execute(
        select(L3Task).where(
            L3Task.project_path == project_path,
            L3Task.task_key == task_key,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {"error": "not_found", "task_key": task_key}

    now = datetime.now(timezone.utc)
    row.status = TaskStatus.closed
    row.closed_at = now
    if content is not None:
        row.content = content
        row.content_hash = compute_content_hash(f"{row.task_key}:{row.title}:{content}:closed")
    row.updated_at = now
    await session.flush()
    out = _row_to_dict(row)
    out["action"] = "closed"
    return out


async def list_tasks(
    session: AsyncSession,
    project_path: str,
    *,
    status: str | None = None,
    order_by: str = "priority",
    limit: int | None = None,
) -> dict:
    capped = _clamp_limit(limit)
    stmt = select(L3Task).where(L3Task.project_path == project_path)
    if status:
        stmt = stmt.where(L3Task.status == TaskStatus(status))
    if order_by == "since_at":
        stmt = stmt.order_by(L3Task.since_at.desc())
    else:
        stmt = stmt.order_by(L3Task.priority.desc(), L3Task.since_at.asc())
    stmt = stmt.limit(capped)
    result = await session.execute(stmt)
    rows = [_row_to_dict(r) for r in result.scalars().all()]
    return {
        "project_path": project_path,
        "status": status,
        "order_by": order_by,
        "count": len(rows),
        "tasks": rows,
    }
