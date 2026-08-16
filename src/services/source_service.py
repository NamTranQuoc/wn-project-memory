from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import DataSource, SourceAddedVia, SourceType
from src.services.project_service import get_or_create_project
from src.services.sanitize import sanitize_and_truncate

USER_SESSION_KEY = "user_session"
LEGACY_UNATTRIBUTED_KEY = "legacy_unattributed"
PROTECTED_REINDEX_SOURCE_KEYS = frozenset({USER_SESSION_KEY, LEGACY_UNATTRIBUTED_KEY})
PROTECTED_REINDEX_SOURCE_TYPES = frozenset(
    {SourceType.user_session, SourceType.local_file}
)


def _source_to_dict(row: DataSource) -> dict:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "project_path": row.project_path,
        "source_key": row.source_key,
        "source_type": row.source_type.value,
        "display_name": sanitize_and_truncate(row.display_name),
        "connection_config": row.connection_config,
        "read_recipe": sanitize_and_truncate(row.read_recipe),
        "added_via": row.added_via.value,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def seed_user_session_source(
    session: AsyncSession,
    *,
    project_id: UUID,
    project_path: str,
) -> DataSource:
    """Ensure the built-in user_session source exists for a project."""
    result = await session.execute(
        select(DataSource).where(
            DataSource.project_id == project_id,
            DataSource.source_key == USER_SESSION_KEY,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    row = DataSource(
        project_id=project_id,
        project_path=project_path,
        source_key=USER_SESSION_KEY,
        source_type=SourceType.user_session,
        display_name="User session decisions",
        connection_config={"kind": "live_chat"},
        read_recipe=(
            "Live agent session decisions — no external re-fetch; "
            "content is the chat utterance itself."
        ),
        added_via=SourceAddedVia.init,
        is_active=True,
    )
    session.add(row)
    await session.flush()
    return row


async def seed_legacy_unattributed_source(
    session: AsyncSession,
    *,
    project_id: UUID,
    project_path: str,
) -> DataSource:
    """Ensure the built-in legacy_unattributed source exists for a project."""
    result = await session.execute(
        select(DataSource).where(
            DataSource.project_id == project_id,
            DataSource.source_key == LEGACY_UNATTRIBUTED_KEY,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    row = DataSource(
        project_id=project_id,
        project_path=project_path,
        source_key=LEGACY_UNATTRIBUTED_KEY,
        source_type=SourceType.other,
        display_name="Legacy unattributed provenance",
        connection_config={"kind": "legacy"},
        read_recipe=(
            "Rows whose source could not be proven during migration; "
            "do not treat as an external crawl target."
        ),
        added_via=SourceAddedVia.init,
        is_active=True,
    )
    session.add(row)
    await session.flush()
    return row


def is_protected_reindex_source(row: DataSource) -> bool:
    return (
        row.source_key in PROTECTED_REINDEX_SOURCE_KEYS
        or row.source_type in PROTECTED_REINDEX_SOURCE_TYPES
    )


async def register_data_source(
    session: AsyncSession,
    project_path: str,
    *,
    source_key: str,
    source_type: str,
    display_name: str | None = None,
    connection_config: dict[str, Any] | None = None,
    read_recipe: str | None = None,
    added_via: str = "manual",
) -> dict:
    project = await get_or_create_project(session, project_path)
    try:
        st = SourceType(source_type)
    except ValueError as exc:
        raise ValueError(
            f"Invalid source_type '{source_type}'. Allowed: {[m.value for m in SourceType]}"
        ) from exc
    try:
        via = SourceAddedVia(added_via)
    except ValueError as exc:
        raise ValueError(
            f"Invalid added_via '{added_via}'. Allowed: {[m.value for m in SourceAddedVia]}"
        ) from exc

    result = await session.execute(
        select(DataSource).where(
            DataSource.project_id == project.id,
            DataSource.source_key == source_key,
        )
    )
    existing = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing is None:
        row = DataSource(
            project_id=project.id,
            project_path=project_path,
            source_key=source_key,
            source_type=st,
            display_name=display_name,
            connection_config=connection_config,
            read_recipe=read_recipe,
            added_via=via,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        payload = _source_to_dict(row)
        payload["action"] = "created"
        return payload

    existing.source_type = st
    existing.display_name = display_name
    existing.connection_config = connection_config
    existing.read_recipe = read_recipe
    existing.added_via = via
    existing.is_active = True
    existing.updated_at = now
    await session.flush()
    payload = _source_to_dict(existing)
    payload["action"] = "updated"
    return payload


async def list_data_sources(
    session: AsyncSession,
    project_path: str,
    *,
    active_only: bool = True,
) -> dict:
    stmt = select(DataSource).where(DataSource.project_path == project_path)
    if active_only:
        stmt = stmt.where(DataSource.is_active.is_(True))
    stmt = stmt.order_by(DataSource.source_key.asc())
    result = await session.execute(stmt)
    rows = [_source_to_dict(r) for r in result.scalars().all()]
    return {
        "project_path": project_path,
        "count": len(rows),
        "sources": rows,
    }


async def deactivate_data_source(
    session: AsyncSession,
    project_path: str,
    source_key: str,
) -> dict:
    result = await session.execute(
        select(DataSource).where(
            DataSource.project_path == project_path,
            DataSource.source_key == source_key,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {"error": "not_found", "source_key": source_key}
    if row.source_key == USER_SESSION_KEY:
        return {"error": "cannot_deactivate_user_session", "source_key": source_key}
    if row.source_key == LEGACY_UNATTRIBUTED_KEY:
        return {"error": "cannot_deactivate_legacy_unattributed", "source_key": source_key}
    row.is_active = False
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    payload = _source_to_dict(row)
    payload["action"] = "deactivated"
    return payload


async def resolve_source_id(
    session: AsyncSession,
    project_path: str,
    source_key: str | None,
    *,
    fallback_user_session: bool = True,
) -> UUID | None:
    """Resolve source_key to id; optionally fall back to user_session."""
    project = await get_or_create_project(session, project_path)
    key = source_key or (USER_SESSION_KEY if fallback_user_session else None)
    if not key:
        return None
    result = await session.execute(
        select(DataSource).where(
            DataSource.project_id == project.id,
            DataSource.source_key == key,
            DataSource.is_active.is_(True),
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row.id
    if key == USER_SESSION_KEY or fallback_user_session:
        seeded = await seed_user_session_source(
            session, project_id=project.id, project_path=project_path
        )
        return seeded.id
    return None
