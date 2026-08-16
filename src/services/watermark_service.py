from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import L3Watermark
from src.services.hashing import compute_content_hash
from src.services.project_service import get_or_create_project
from src.services.source_service import resolve_source_id


def _watermark_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _row_to_dict(row: L3Watermark) -> dict:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "project_path": row.project_path,
        "source_id": str(row.source_id),
        "raw_event_id": str(row.raw_event_id) if row.raw_event_id else None,
        "l3_entity_id": str(row.l3_entity_id) if row.l3_entity_id else None,
        "stream_key": row.stream_key or "",
        "indexed_through": row.indexed_through,
        "full_read_ids": row.full_read_ids,
        "known_gaps": row.known_gaps,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        "content_hash": row.content_hash,
        "source_hash": row.source_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def upsert_watermark(
    session: AsyncSession,
    project_path: str,
    *,
    source_key: str,
    stream_key: str | None = None,
    indexed_through: dict[str, Any] | None = None,
    full_read_ids: list[Any] | None = None,
    known_gaps: list[Any] | None = None,
    checked_at: datetime | str | None = None,
    raw_event_id: uuid.UUID | str | None = None,
    l3_entity_id: uuid.UUID | str | None = None,
    source_hash: str | None = None,
) -> dict:
    project = await get_or_create_project(session, project_path)
    source_id = await resolve_source_id(
        session, project_path, source_key, fallback_user_session=False
    )
    if source_id is None:
        raise ValueError(f"Unknown or inactive source_key '{source_key}'")

    stream = stream_key or ""
    checked = checked_at
    if isinstance(checked, str):
        checked = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    if checked is None:
        checked = datetime.now(timezone.utc)

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

    payload = {
        "indexed_through": indexed_through,
        "full_read_ids": full_read_ids,
        "known_gaps": known_gaps,
        "checked_at": checked.isoformat(),
        "stream_key": stream,
    }
    content_hash = compute_content_hash(_watermark_payload(payload))
    hash_value = source_hash or content_hash

    result = await session.execute(
        select(L3Watermark).where(
            L3Watermark.project_id == project.id,
            L3Watermark.source_id == source_id,
            L3Watermark.stream_key == stream,
        )
    )
    existing = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if existing is None:
        row = L3Watermark(
            project_id=project.id,
            project_path=project_path,
            source_id=source_id,
            raw_event_id=event_id,
            l3_entity_id=entity_id,
            stream_key=stream,
            indexed_through=indexed_through,
            full_read_ids=full_read_ids,
            known_gaps=known_gaps,
            checked_at=checked,
            content_hash=content_hash,
            source_hash=hash_value,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        out = _row_to_dict(row)
        out["action"] = "created"
        return out

    # Cursor upsert always refreshes fields — watermarks are state, not append-only.
    # Contract: callers must only advance indexed_through after the corresponding
    # source units were successfully ingested (and structured writes done). Never set
    # the cursor to "now" after a skipped/failed/429 fetch. Recommended shapes:
    #   git:    {"commit": "<sha>", "path"?: "..."} or {"tree": "<sha>"} / {"blob": "<sha>"}
    #   teams:  {"message_id": "...", "created_at": "<iso8601>"}
    #   github: {"updated_at": "<iso8601>", "id": <int>}
    existing.indexed_through = indexed_through
    existing.full_read_ids = full_read_ids
    existing.known_gaps = known_gaps
    existing.checked_at = checked
    existing.raw_event_id = event_id if event_id is not None else existing.raw_event_id
    existing.l3_entity_id = entity_id if entity_id is not None else existing.l3_entity_id
    existing.content_hash = content_hash
    existing.source_hash = hash_value
    existing.updated_at = now
    await session.flush()
    out = _row_to_dict(existing)
    out["action"] = "updated"
    return out


async def get_watermark(
    session: AsyncSession,
    project_path: str,
    *,
    source_key: str,
    stream_key: str | None = None,
) -> dict:
    source_id = await resolve_source_id(
        session, project_path, source_key, fallback_user_session=False
    )
    if source_id is None:
        return {"error": "not_found", "source_key": source_key}
    stream = stream_key or ""
    result = await session.execute(
        select(L3Watermark).where(
            L3Watermark.project_path == project_path,
            L3Watermark.source_id == source_id,
            L3Watermark.stream_key == stream,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {
            "error": "not_found",
            "source_key": source_key,
            "stream_key": stream,
        }
    return _row_to_dict(row)


async def list_watermarks(
    session: AsyncSession,
    project_path: str,
) -> dict:
    result = await session.execute(
        select(L3Watermark)
        .where(L3Watermark.project_path == project_path)
        .order_by(L3Watermark.checked_at.desc().nullslast())
    )
    rows = [_row_to_dict(r) for r in result.scalars().all()]
    return {"project_path": project_path, "count": len(rows), "watermarks": rows}
