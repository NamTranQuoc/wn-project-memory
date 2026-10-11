"""L4 · raw events: write, read with touch, and fall back to the real source link."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import Fact, RawEvent, Revision, RevisionSource, Source
from src.services.common import (
    check_length,
    clean_offset,
    iso,
    now_utc,
    parse_uuid,
    require_text,
)
from src.services.hashing import compute_source_hash
from src.services.l1_service import get_or_create_project, require_project, resolve_source
from src.services.sanitize import sanitize_and_truncate
from src.services.secret_redactor import redact_known_secrets, redact_secrets


async def touch_raw_event(session: AsyncSession, project_id: uuid.UUID, raw_event_id: uuid.UUID) -> bool:
    """Renew a raw event. Writes at most once per L4_TOUCH_MIN_DAYS.

    Changing the partition key makes Postgres move the row into this month's partition,
    so the daily job that drops old partitions keeps it for another retention window.
    Returns False when the row was touched recently or is already gone.
    """
    result = await session.execute(
        text(
            """
            UPDATE l4_raw_events
            SET last_touched_at = now()
            WHERE id = :id
              AND project_id = :project_id
              AND last_touched_at < now() - make_interval(days => :min_days)
            """
        ),
        {"id": raw_event_id, "project_id": project_id, "min_days": get_settings().l4_touch_min_days},
    )
    return bool(result.rowcount)


def _raw_to_dict(event: RawEvent) -> dict:
    return {
        "raw_event_id": str(event.id),
        "event_type": event.event_type,
        "source_url": event.source_url,
        "source_hash": event.source_hash,
        "created_at": iso(event.created_at),
        "last_touched_at": iso(event.last_touched_at),
        "raw_content": sanitize_and_truncate(event.raw_content),
    }


async def add_raw_event(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    project_path: str,
    source_id: uuid.UUID | None,
    event_type: str,
    content: str,
    source_hash: str,
    source_url: str | None,
) -> RawEvent:
    """The only writer of l4_raw_events. Secrets are redacted before the payload is stored;
    callers hash the original text, so re-fetching the same unit still matches."""
    check_length(content, field="content", limit=get_settings().max_raw_chars)
    now = now_utc()
    event = RawEvent(
        project_id=project_id,
        project_path=project_path,
        source_id=source_id,
        event_type=event_type,
        raw_content=redact_secrets(content),
        source_hash=source_hash,
        source_url=redact_known_secrets(source_url),
        created_at=now,
        last_touched_at=now,
    )
    session.add(event)
    await session.flush()
    return event


async def log_raw_event(
    session: AsyncSession,
    project_path: str,
    *,
    event_type: str,
    content: str,
    source_key: str | None = None,
    source_url: str | None = None,
    source_hash: str | None = None,
) -> dict:
    """Store a raw payload. Use ingest_source_unit for crawled sources; this is for live chat."""
    project = await get_or_create_project(session, project_path)
    source = await resolve_source(session, project, source_key)
    body = require_text(content, field="content")
    event = await add_raw_event(
        session,
        project_id=project.id,
        project_path=project.project_path,
        source_id=source.id,
        event_type=require_text(event_type, field="event_type"),
        content=body,
        source_hash=source_hash or compute_source_hash(body),
        source_url=source_url,
    )
    return {"action": "created", "source_key": source.source_key, **_raw_to_dict(event)}


async def source_urls_for_raw(
    session: AsyncSession, project_id: uuid.UUID, raw_event_id: uuid.UUID
) -> list[str]:
    """Real-source links that L3 revisions kept for a raw event, newest first."""
    result = await session.execute(
        select(RevisionSource.source_url)
        .join(Revision, Revision.id == RevisionSource.revision_id)
        .join(Fact, Fact.id == Revision.fact_id)
        .where(
            RevisionSource.raw_event_id == raw_event_id,
            Fact.project_id == project_id,
            RevisionSource.source_url.is_not(None),
        )
        .order_by(Revision.changed_at.desc())
        .limit(get_settings().default_limit)
    )
    return list(dict.fromkeys(row[0] for row in result.all()))


async def get_raw_context(
    session: AsyncSession,
    project_path: str,
    raw_event_id: str,
    *,
    offset: int | None = None,
    max_chars: int | None = None,
) -> dict:
    """The raw payload, one page at a time. Reading the first page counts as a touch.

    If retention already dropped the row, return the real-source links instead so the
    agent can re-read the live source with that source's read_recipe.
    """
    project = await require_project(session, project_path)
    event_id = parse_uuid(raw_event_id, field="raw_event_id")
    result = await session.execute(
        select(RawEvent, Source.source_key)
        .outerjoin(Source, Source.id == RawEvent.source_id)
        .where(RawEvent.id == event_id, RawEvent.project_id == project.id)
    )
    found = result.first()
    if found is None:
        urls = await source_urls_for_raw(session, project.id, event_id)
        return {
            "error": "raw_dropped",
            "raw_event_id": str(event_id),
            "source_urls": urls,
            "hint": "read the real source with the read_recipe of its source (list_data_sources)",
        }
    event, source_key = found
    settings = get_settings()
    start = clean_offset(offset)
    size = max(1, min(int(max_chars or settings.raw_page_chars), settings.max_raw_page_chars))
    if start == 0:
        await touch_raw_event(session, project.id, event_id)
    total = len(event.raw_content)
    end = min(start + size, total)
    return {
        "source_key": source_key,
        **_raw_to_dict(event),
        "raw_content": event.raw_content[start:end],
        "offset": start,
        "total_chars": total,
        "next_offset": end if end < total else None,
    }
