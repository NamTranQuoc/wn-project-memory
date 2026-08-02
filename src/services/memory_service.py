from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import L0WorkingMemory, L2MetaMemory, L3DistilledKnowledge, L4RawEvent
from src.services.hashing import compute_content_hash, compute_source_hash
from src.services.project_service import get_or_create_project
from src.services.sanitize import sanitize_and_truncate
from src.services.source_service import (
    register_data_source,
    resolve_source_id,
    seed_user_session_source,
)


async def init_project_memory(
    session: AsyncSession,
    project_path: str,
    initial_context: str,
    sources: list[dict] | None = None,
) -> dict:
    """Initialize L0/L2, project registry, and optional data sources."""
    project = await get_or_create_project(session, project_path)

    l2_result = await session.execute(
        select(L2MetaMemory).where(L2MetaMemory.project_path == project_path)
    )
    l2 = l2_result.scalar_one_or_none()
    if l2 is None:
        l2 = L2MetaMemory(
            project_id=project.id,
            project_path=project_path,
            environment_setup=initial_context,
            project_structure=initial_context,
        )
        session.add(l2)
    else:
        l2.project_id = project.id
        l2.environment_setup = initial_context
        l2.project_structure = initial_context

    l0_result = await session.execute(
        select(L0WorkingMemory).where(L0WorkingMemory.project_path == project_path)
    )
    l0 = l0_result.scalar_one_or_none()
    if l0 is None:
        l0 = L0WorkingMemory(
            project_id=project.id,
            project_path=project_path,
            current_focus_text="Initialized",
        )
        session.add(l0)
    else:
        l0.project_id = project.id

    await seed_user_session_source(session, project_id=project.id, project_path=project_path)

    registered_sources: list[dict] = []
    for src in sources or []:
        registered_sources.append(
            await register_data_source(
                session,
                project_path,
                source_key=str(src["source_key"]),
                source_type=str(src.get("source_type", "other")),
                display_name=src.get("display_name"),
                connection_config=src.get("connection_config"),
                read_recipe=src.get("read_recipe"),
                added_via="init",
            )
        )

    await session.flush()
    return {
        "project_path": project_path,
        "project_id": str(project.id),
        "l0_id": str(l0.id),
        "l2_id": str(l2.id),
        "status": "initialized",
        "environment_setup": sanitize_and_truncate(l2.environment_setup),
        "sources_registered": registered_sources,
    }


async def update_working_memory(
    session: AsyncSession,
    project_path: str,
    current_focus_text: str,
) -> dict:
    project = await get_or_create_project(session, project_path)
    result = await session.execute(
        select(L0WorkingMemory).where(L0WorkingMemory.project_path == project_path)
    )
    l0 = result.scalar_one_or_none()
    if l0 is None:
        l0 = L0WorkingMemory(
            project_id=project.id,
            project_path=project_path,
            current_focus_text=current_focus_text,
        )
        session.add(l0)
    else:
        l0.project_id = project.id
        l0.current_focus_text = current_focus_text
        l0.updated_at = datetime.now(timezone.utc)

    await session.flush()
    return {
        "project_path": project_path,
        "project_id": str(project.id),
        "current_focus_text": sanitize_and_truncate(l0.current_focus_text),
        "updated_at": l0.updated_at.isoformat() if l0.updated_at else None,
    }


async def upsert_distilled_rule(
    session: AsyncSession,
    project_path: str,
    entity_key: str,
    content: str,
    raw_event_id: uuid.UUID | str | None,
    source_hash: str,
    embedding: list[float] | None = None,
) -> dict:
    """Upsert L3 rule. Overwrite when content_hash or source_hash changes."""
    project = await get_or_create_project(session, project_path)
    content_hash = compute_content_hash(content)
    event_id: uuid.UUID | None = None
    if raw_event_id is not None:
        event_id = (
            raw_event_id if isinstance(raw_event_id, uuid.UUID) else uuid.UUID(str(raw_event_id))
        )

    result = await session.execute(
        select(L3DistilledKnowledge).where(
            L3DistilledKnowledge.project_path == project_path,
            L3DistilledKnowledge.entity_key == entity_key,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        row = L3DistilledKnowledge(
            project_id=project.id,
            project_path=project_path,
            entity_key=entity_key,
            content=content,
            content_hash=content_hash,
            source_hash=source_hash,
            raw_event_id=event_id,
            embedding=embedding,
            last_verified_at=datetime.now(timezone.utc),
        )
        session.add(row)
        await session.flush()
        return {
            "action": "created",
            "id": str(row.id),
            "project_id": str(project.id),
            "entity_key": entity_key,
            "content": sanitize_and_truncate(content),
            "content_hash": content_hash,
            "source_hash": source_hash,
            "raw_event_id": str(event_id) if event_id else None,
        }

    hashes_changed = existing.content_hash != content_hash or existing.source_hash != source_hash
    if hashes_changed:
        existing.project_id = project.id
        existing.content = content
        existing.content_hash = content_hash
        existing.source_hash = source_hash
        existing.raw_event_id = event_id
        if embedding is not None:
            existing.embedding = embedding
        existing.last_verified_at = datetime.now(timezone.utc)
        await session.flush()
        return {
            "action": "overwritten",
            "id": str(existing.id),
            "project_id": str(project.id),
            "entity_key": entity_key,
            "content": sanitize_and_truncate(content),
            "content_hash": content_hash,
            "source_hash": source_hash,
            "raw_event_id": str(event_id) if event_id else None,
        }

    existing.project_id = project.id
    existing.last_verified_at = datetime.now(timezone.utc)
    await session.flush()
    return {
        "action": "unchanged",
        "id": str(existing.id),
        "project_id": str(project.id),
        "entity_key": entity_key,
        "content": sanitize_and_truncate(existing.content),
        "content_hash": existing.content_hash,
        "source_hash": existing.source_hash,
        "raw_event_id": str(existing.raw_event_id) if existing.raw_event_id else None,
    }


async def log_raw_event(
    session: AsyncSession,
    project_path: str,
    event_type: str,
    content: str,
    source_hash: str | None = None,
    source_key: str | None = None,
) -> dict:
    project = await get_or_create_project(session, project_path)
    source_id = await resolve_source_id(
        session, project_path, source_key, fallback_user_session=True
    )
    hash_value = source_hash or compute_source_hash(content)
    event = L4RawEvent(
        project_id=project.id,
        project_path=project_path,
        source_id=source_id,
        event_type=event_type,
        raw_content=content,
        source_hash=hash_value,
    )
    session.add(event)
    await session.flush()
    return {
        "id": str(event.id),
        "project_path": project_path,
        "project_id": str(project.id),
        "source_id": str(source_id) if source_id else None,
        "event_type": event_type,
        "source_hash": hash_value,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "raw_content": sanitize_and_truncate(content),
    }


async def get_raw_context(
    session: AsyncSession,
    project_path: str,
    raw_event_id: uuid.UUID | str,
) -> dict:
    event_id = raw_event_id if isinstance(raw_event_id, uuid.UUID) else uuid.UUID(str(raw_event_id))
    result = await session.execute(
        select(L4RawEvent).where(
            L4RawEvent.id == event_id,
            L4RawEvent.project_path == project_path,
        )
    )
    event = result.scalar_one_or_none()
    if event is None:
        return {"error": "not_found", "raw_event_id": str(event_id)}

    return {
        "id": str(event.id),
        "project_path": event.project_path,
        "project_id": str(event.project_id) if event.project_id else None,
        "source_id": str(event.source_id) if event.source_id else None,
        "event_type": event.event_type,
        "raw_content": event.raw_content,
        "source_hash": event.source_hash,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
