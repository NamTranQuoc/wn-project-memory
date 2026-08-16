"""Idempotent source-unit ingest ledger (L3 durable, independent of L4 retention)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import L3SourceUnit, L4RawEvent
from src.services.hashing import (
    build_item_key,
    canonicalize_content,
    compute_content_hash,
    compute_source_hash,
)
from src.services.project_service import get_or_create_project
from src.services.sanitize import sanitize_and_truncate
from src.services.source_service import resolve_source_id


def _clamp_limit(limit: int | None) -> int:
    settings = get_settings()
    if limit is None:
        return settings.default_search_limit
    return max(1, min(int(limit), settings.max_search_limit))


def _row_to_dict(row: L3SourceUnit) -> dict:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "project_path": row.project_path,
        "source_id": str(row.source_id),
        "stream_key": row.stream_key or "",
        "item_key": row.item_key,
        "external_id": row.external_id,
        "content_hash": row.content_hash,
        "source_hash": row.source_hash,
        "last_raw_event_id": str(row.last_raw_event_id) if row.last_raw_event_id else None,
        "unit_metadata": row.unit_metadata,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _resolve_hashes(
    *,
    content: str | None,
    content_hash: str | None,
    source_hash: str | None,
) -> tuple[str, str, str]:
    """Return (canonical_content_or_empty, content_hash, source_hash)."""
    canonical = canonicalize_content(content) if content is not None else ""
    if content_hash:
        c_hash = content_hash.strip().lower()
    elif content is not None:
        c_hash = compute_content_hash(canonical)
    else:
        c_hash = ""
    if source_hash:
        s_hash = source_hash.strip().lower()
    elif content is not None:
        s_hash = compute_source_hash(canonical)
    else:
        s_hash = ""
    return canonical, c_hash, s_hash


def _hashes_match(existing: L3SourceUnit, content_hash: str, source_hash: str) -> bool:
    if content_hash and existing.content_hash != content_hash:
        return False
    if source_hash and existing.source_hash != source_hash:
        return False
    # If caller provided neither comparable hash, treat as known-but-ambiguous → changed
    # so agent re-reads rather than silently skipping.
    if not content_hash and not source_hash:
        return False
    return True


async def _lookup_unit(
    session: AsyncSession,
    *,
    project_id: UUID,
    source_id: UUID,
    stream_key: str,
    item_key: str,
) -> L3SourceUnit | None:
    result = await session.execute(
        select(L3SourceUnit).where(
            L3SourceUnit.project_id == project_id,
            L3SourceUnit.source_id == source_id,
            L3SourceUnit.stream_key == stream_key,
            L3SourceUnit.item_key == item_key,
        )
    )
    return result.scalar_one_or_none()


async def ingest_source_unit(
    session: AsyncSession,
    project_path: str,
    *,
    source_key: str,
    content: str,
    stream_key: str | None = None,
    external_id: str | None = None,
    source_hash: str | None = None,
    event_type: str = "source_unit",
    unit_metadata: dict[str, Any] | None = None,
) -> dict:
    """Idempotent ingest of one source unit.

    Actions:
    - created: first sight of this item_key → append L4 + create ledger row
    - changed: same item_key, different content/source hash → append new L4 + update ledger
    - unchanged: hashes match → touch last_seen_at only, no new L4
    """
    if not content or not content.strip():
        raise ValueError("content must be non-empty")

    project = await get_or_create_project(session, project_path)
    source_id = await resolve_source_id(
        session, project_path, source_key, fallback_user_session=False
    )
    if source_id is None:
        raise ValueError(f"Unknown or inactive source_key '{source_key}'")

    stream = stream_key or ""
    canonical, content_hash, native_or_computed_hash = _resolve_hashes(
        content=content,
        content_hash=None,
        source_hash=source_hash,
    )
    cleaned_external = external_id.strip() if external_id and external_id.strip() else None
    item_key = build_item_key(external_id=cleaned_external, content_hash=content_hash)
    now = datetime.now(timezone.utc)

    existing = await _lookup_unit(
        session,
        project_id=project.id,
        source_id=source_id,
        stream_key=stream,
        item_key=item_key,
    )

    if existing is not None and _hashes_match(
        existing, content_hash, native_or_computed_hash
    ):
        existing.last_seen_at = now
        existing.updated_at = now
        await session.flush()
        out = _row_to_dict(existing)
        out["action"] = "unchanged"
        out["raw_event_id"] = out["last_raw_event_id"]
        out["raw_content"] = sanitize_and_truncate(canonical)
        return out

    event = L4RawEvent(
        project_id=project.id,
        project_path=project_path,
        source_id=source_id,
        event_type=event_type,
        raw_content=canonical,
        source_hash=native_or_computed_hash,
    )
    session.add(event)
    await session.flush()

    if existing is None:
        row = L3SourceUnit(
            project_id=project.id,
            project_path=project_path,
            source_id=source_id,
            stream_key=stream,
            item_key=item_key,
            external_id=cleaned_external,
            content_hash=content_hash,
            source_hash=native_or_computed_hash,
            last_raw_event_id=event.id,
            unit_metadata=unit_metadata,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        out = _row_to_dict(row)
        out["action"] = "created"
        out["raw_event_id"] = str(event.id)
        out["raw_content"] = sanitize_and_truncate(canonical)
        return out

    existing.content_hash = content_hash
    existing.source_hash = native_or_computed_hash
    existing.last_raw_event_id = event.id
    existing.last_seen_at = now
    existing.updated_at = now
    if cleaned_external is not None:
        existing.external_id = cleaned_external
    if unit_metadata is not None:
        existing.unit_metadata = unit_metadata
    await session.flush()
    out = _row_to_dict(existing)
    out["action"] = "changed"
    out["raw_event_id"] = str(event.id)
    out["raw_content"] = sanitize_and_truncate(canonical)
    return out


async def check_source_units(
    session: AsyncSession,
    project_path: str,
    *,
    source_key: str,
    candidates: list[dict[str, Any]],
    stream_key: str | None = None,
    limit: int | None = None,
) -> dict:
    """Batch-check whether candidates are unknown / unchanged / changed.

    Each candidate may include: external_id, content, content_hash, source_hash,
    stream_key (overrides top-level), index (echoed back).
    Result count is hard-capped (default/max = 5).
    """
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")

    capped = _clamp_limit(limit)
    project = await get_or_create_project(session, project_path)
    source_id = await resolve_source_id(
        session, project_path, source_key, fallback_user_session=False
    )
    if source_id is None:
        raise ValueError(f"Unknown or inactive source_key '{source_key}'")

    default_stream = stream_key or ""
    results: list[dict] = []
    for idx, raw in enumerate(candidates[:capped]):
        if not isinstance(raw, dict):
            results.append(
                {
                    "index": idx,
                    "status": "error",
                    "error": "candidate must be an object",
                }
            )
            continue

        cand_stream = raw.get("stream_key")
        stream = str(cand_stream) if cand_stream is not None else default_stream
        external_id = raw.get("external_id")
        content = raw.get("content")
        content_hash_arg = raw.get("content_hash")
        source_hash_arg = raw.get("source_hash")

        if content is None and not content_hash_arg and not external_id and not source_hash_arg:
            results.append(
                {
                    "index": raw.get("index", idx),
                    "status": "error",
                    "error": "need external_id, content, content_hash, or source_hash",
                }
            )
            continue

        _, content_hash, native_hash = _resolve_hashes(
            content=content if isinstance(content, str) else None,
            content_hash=str(content_hash_arg) if content_hash_arg else None,
            source_hash=str(source_hash_arg) if source_hash_arg else None,
        )
        cleaned_external = (
            str(external_id).strip()
            if external_id is not None and str(external_id).strip()
            else None
        )
        if not content_hash and cleaned_external is None:
            # source_hash alone without identity — look up by source_hash within stream
            result = await session.execute(
                select(L3SourceUnit).where(
                    L3SourceUnit.project_id == project.id,
                    L3SourceUnit.source_id == source_id,
                    L3SourceUnit.stream_key == stream,
                    L3SourceUnit.source_hash == native_hash,
                )
            )
            row = result.scalars().first()
            if row is None:
                results.append(
                    {
                        "index": raw.get("index", idx),
                        "status": "unknown",
                        "source_hash": native_hash,
                        "stream_key": stream,
                    }
                )
            elif _hashes_match(row, content_hash, native_hash):
                payload = _row_to_dict(row)
                payload["index"] = raw.get("index", idx)
                payload["status"] = "unchanged"
                results.append(payload)
            else:
                payload = _row_to_dict(row)
                payload["index"] = raw.get("index", idx)
                payload["status"] = "changed"
                results.append(payload)
            continue

        if not content_hash and cleaned_external is not None:
            # Identity-only check: existence by external_id item_key.
            # Without a hash to compare, status is unknown if missing, else known_present
            # with hashes so agent can decide — treat missing hash compare as "changed"
            # only when caller also supplied a hash. With only external_id → "known".
            item_key = build_item_key(external_id=cleaned_external, content_hash="")
            # build_item_key with empty content_hash still yields ext:id
            row = await _lookup_unit(
                session,
                project_id=project.id,
                source_id=source_id,
                stream_key=stream,
                item_key=item_key,
            )
            if row is None:
                results.append(
                    {
                        "index": raw.get("index", idx),
                        "status": "unknown",
                        "item_key": item_key,
                        "external_id": cleaned_external,
                        "stream_key": stream,
                    }
                )
            elif native_hash or content_hash:
                payload = _row_to_dict(row)
                payload["index"] = raw.get("index", idx)
                payload["status"] = (
                    "unchanged" if _hashes_match(row, content_hash, native_hash) else "changed"
                )
                results.append(payload)
            else:
                payload = _row_to_dict(row)
                payload["index"] = raw.get("index", idx)
                payload["status"] = "known"
                results.append(payload)
            continue

        item_key = build_item_key(external_id=cleaned_external, content_hash=content_hash)
        row = await _lookup_unit(
            session,
            project_id=project.id,
            source_id=source_id,
            stream_key=stream,
            item_key=item_key,
        )
        if row is None:
            results.append(
                {
                    "index": raw.get("index", idx),
                    "status": "unknown",
                    "item_key": item_key,
                    "external_id": cleaned_external,
                    "content_hash": content_hash or None,
                    "source_hash": native_hash or None,
                    "stream_key": stream,
                }
            )
        elif _hashes_match(row, content_hash, native_hash):
            payload = _row_to_dict(row)
            payload["index"] = raw.get("index", idx)
            payload["status"] = "unchanged"
            results.append(payload)
        else:
            payload = _row_to_dict(row)
            payload["index"] = raw.get("index", idx)
            payload["status"] = "changed"
            results.append(payload)

    return {
        "project_path": project_path,
        "source_key": source_key,
        "count": len(results),
        "limit_applied": capped,
        "results": results,
    }


async def get_source_unit(
    session: AsyncSession,
    project_path: str,
    *,
    source_key: str,
    item_key: str | None = None,
    external_id: str | None = None,
    stream_key: str | None = None,
) -> dict:
    """Lookup one ledger row by item_key or external_id."""
    source_id = await resolve_source_id(
        session, project_path, source_key, fallback_user_session=False
    )
    if source_id is None:
        return {"error": "not_found", "source_key": source_key}

    stream = stream_key or ""
    key = item_key
    if not key and external_id and external_id.strip():
        key = build_item_key(external_id=external_id.strip(), content_hash="")
    if not key:
        raise ValueError("item_key or external_id is required")

    project = await get_or_create_project(session, project_path)
    row = await _lookup_unit(
        session,
        project_id=project.id,
        source_id=source_id,
        stream_key=stream,
        item_key=key,
    )
    if row is None:
        return {
            "error": "not_found",
            "source_key": source_key,
            "stream_key": stream,
            "item_key": key,
        }
    return _row_to_dict(row)
