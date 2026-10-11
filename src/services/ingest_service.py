"""Ingest ledger: idempotent per-unit ingest into L4, plus per-stream watermarks.

The service never fetches external systems. The agent runs each source's read_recipe
with its own tools, then calls check_source_units → ingest_source_unit → write_fact →
upsert_watermark.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import IngestUnit, IngestWatermark, Project, Source
from src.services.common import NotFoundError, iso, list_limit, now_utc, require_text, search_limit
from src.services.hashing import (
    build_item_key,
    canonicalize_content,
    compute_content_hash,
    compute_source_hash,
)
from src.services.l1_service import USER_SESSION_KEY, require_project, resolve_source
from src.services.raw_service import add_raw_event
from src.services.sanitize import sanitize_and_truncate
from src.services.secret_redactor import redact_mapping


def _unit_to_dict(row: IngestUnit) -> dict:
    return {
        "stream_key": row.stream_key or "",
        "item_key": row.item_key,
        "external_id": row.external_id,
        "content_hash": row.content_hash,
        "source_hash": row.source_hash,
        "last_raw_event_id": str(row.last_raw_event_id) if row.last_raw_event_id else None,
        "unit_metadata": row.unit_metadata,
        "first_seen_at": iso(row.first_seen_at),
        "last_seen_at": iso(row.last_seen_at),
    }


def _clean_external(external_id: Any) -> str | None:
    if external_id is None:
        return None
    cleaned = str(external_id).strip()
    return cleaned or None


async def _crawl_source(session: AsyncSession, project_path: str, source_key: str) -> tuple[Project, Source]:
    project = await require_project(session, project_path)
    key = require_text(source_key, field="source_key")
    if key == USER_SESSION_KEY:
        raise ValueError("user_session is not crawled; use log_raw_event for live decisions")
    return project, await resolve_source(session, project, key)


async def _lookup(session: AsyncSession, source_id, stream: str, item_key: str) -> IngestUnit | None:
    result = await session.execute(
        select(IngestUnit).where(
            IngestUnit.source_id == source_id,
            IngestUnit.stream_key == stream,
            IngestUnit.item_key == item_key,
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
    source_url: str | None = None,
    event_type: str = "source_unit",
    unit_metadata: dict[str, Any] | None = None,
) -> dict:
    """created → new L4 row + ledger row · changed → new L4 row · unchanged → no L4 write."""
    project, source = await _crawl_source(session, project_path, source_key)
    canonical = canonicalize_content(require_text(content, field="content"))
    content_hash = compute_content_hash(canonical)
    native_hash = (source_hash or "").strip().lower() or compute_source_hash(canonical)
    ext = _clean_external(external_id)
    item_key = build_item_key(external_id=ext, content_hash=content_hash)
    stream = stream_key or ""
    now = now_utc()

    row = await _lookup(session, source.id, stream, item_key)
    if row is not None and row.content_hash == content_hash and row.source_hash == native_hash:
        row.last_seen_at = now
        await session.flush()
        return {"action": "unchanged", "raw_event_id": _unit_to_dict(row)["last_raw_event_id"],
                **_unit_to_dict(row)}

    event = await add_raw_event(
        session,
        project_id=project.id,
        project_path=project.project_path,
        source_id=source.id,
        event_type=event_type,
        content=canonical,
        source_hash=native_hash,
        source_url=source_url,
    )
    action = "changed"
    if row is None:
        row = IngestUnit(
            source_id=source.id,
            stream_key=stream,
            item_key=item_key,
            external_id=ext,
            first_seen_at=now,
            content_hash=content_hash,
            source_hash=native_hash,
        )
        session.add(row)
        action = "created"
    row.content_hash = content_hash
    row.source_hash = native_hash
    row.last_raw_event_id = event.id
    row.last_seen_at = now
    if unit_metadata is not None:
        row.unit_metadata = redact_mapping(unit_metadata)
    await session.flush()
    return {
        "action": action,
        "raw_event_id": str(event.id),
        "raw_content": sanitize_and_truncate(event.raw_content),
        **{k: v for k, v in _unit_to_dict(row).items() if k != "last_raw_event_id"},
    }


async def check_source_units(
    session: AsyncSession,
    project_path: str,
    *,
    source_key: str,
    candidates: list[dict[str, Any]],
    stream_key: str | None = None,
    limit: int | None = None,
) -> dict:
    """Classify candidates as unknown / unchanged / changed / known before reading bodies.

    A candidate may carry external_id, content, content_hash, source_hash, stream_key
    and an index that is echoed back. At most 5 candidates are checked per call.
    """
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    _, source = await _crawl_source(session, project_path, source_key)
    capped = search_limit(limit)
    results: list[dict] = []
    for idx, cand in enumerate(candidates[:capped]):
        index = cand.get("index", idx) if isinstance(cand, dict) else idx
        if not isinstance(cand, dict):
            results.append({"index": index, "status": "error", "error": "candidate must be an object"})
            continue
        stream = str(cand["stream_key"]) if cand.get("stream_key") is not None else (stream_key or "")
        ext = _clean_external(cand.get("external_id"))
        content = cand.get("content")
        c_hash = (cand.get("content_hash") or "").strip().lower()
        if not c_hash and isinstance(content, str) and content.strip():
            c_hash = compute_content_hash(canonicalize_content(content))
        s_hash = (cand.get("source_hash") or "").strip().lower()
        if not (ext or c_hash or s_hash):
            results.append({"index": index, "status": "error",
                            "error": "need external_id, content, content_hash or source_hash"})
            continue

        if ext or c_hash:
            item_key = build_item_key(external_id=ext, content_hash=c_hash)
            row = await _lookup(session, source.id, stream, item_key)
        else:
            row = (
                await session.execute(
                    select(IngestUnit).where(
                        IngestUnit.source_id == source.id,
                        IngestUnit.stream_key == stream,
                        IngestUnit.source_hash == s_hash,
                    )
                )
            ).scalars().first()
            item_key = row.item_key if row else None

        if row is None:
            results.append({"index": index, "status": "unknown", "item_key": item_key,
                            "external_id": ext, "stream_key": stream})
            continue
        if not c_hash and not s_hash:
            status = "known"
        elif (c_hash and row.content_hash != c_hash) or (s_hash and row.source_hash != s_hash):
            status = "changed"
        else:
            status = "unchanged"
        results.append({"index": index, "status": status, **_unit_to_dict(row)})
    return {"source_key": source.source_key, "count": len(results), "limit": capped,
            "results": results}


async def get_source_unit(
    session: AsyncSession,
    project_path: str,
    *,
    source_key: str,
    item_key: str | None = None,
    external_id: str | None = None,
    stream_key: str | None = None,
) -> dict:
    _, source = await _crawl_source(session, project_path, source_key)
    ext = _clean_external(external_id)
    key = item_key or (build_item_key(external_id=ext, content_hash="") if ext else None)
    if not key:
        raise ValueError("item_key or external_id is required")
    row = await _lookup(session, source.id, stream_key or "", key)
    if row is None:
        raise NotFoundError("source_unit", source_key=source.source_key, item_key=key)
    return _unit_to_dict(row)


# ── watermarks ──────────────────────────────────────────────────────────────


def _wm_to_dict(source_key: str, row: IngestWatermark) -> dict:
    return {
        "source_key": source_key,
        "stream_key": row.stream_key or "",
        "indexed_through": row.indexed_through,
        "full_read_ids": row.full_read_ids,
        "known_gaps": row.known_gaps,
        "checked_at": iso(row.checked_at),
        "updated_at": iso(row.updated_at),
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
    checked_at: str | None = None,
) -> dict:
    """Advance a cursor. Call only after the units up to it were ingested and written.

    Never move it after a skipped, failed or rate-limited fetch.
    """
    _, source = await _crawl_source(session, project_path, source_key)
    stream = stream_key or ""
    checked = (
        datetime.fromisoformat(checked_at.replace("Z", "+00:00")) if checked_at else now_utc()
    )
    row = await session.get(IngestWatermark, (source.id, stream))
    action = "updated"
    if row is None:
        row = IngestWatermark(source_id=source.id, stream_key=stream)
        session.add(row)
        action = "created"
    row.indexed_through = indexed_through
    row.full_read_ids = full_read_ids
    row.known_gaps = known_gaps
    row.checked_at = checked
    row.updated_at = now_utc()
    await session.flush()
    return {"action": action, **_wm_to_dict(source.source_key, row)}


async def get_watermark(
    session: AsyncSession, project_path: str, *, source_key: str, stream_key: str | None = None
) -> dict:
    _, source = await _crawl_source(session, project_path, source_key)
    row = await session.get(IngestWatermark, (source.id, stream_key or ""))
    if row is None:
        raise NotFoundError("watermark", source_key=source.source_key, stream_key=stream_key or "")
    return _wm_to_dict(source.source_key, row)


async def list_watermarks(session: AsyncSession, project_path: str, *, limit: int | None = None) -> dict:
    project = await require_project(session, project_path)
    capped = list_limit(limit)
    rows = (
        await session.execute(
            select(IngestWatermark, Source.source_key)
            .join(Source, Source.id == IngestWatermark.source_id)
            .where(Source.project_id == project.id)
            .order_by(IngestWatermark.checked_at.desc().nulls_last())
            .limit(capped)
        )
    ).all()
    return {"items": [_wm_to_dict(key, row) for row, key in rows], "limit": capped}


async def reset_source_ingest(
    session: AsyncSession, project_path: str, *, source_key: str, confirm: bool = False
) -> dict:
    """Forget what was crawled from one source so a full re-crawl starts cold.

    Facts are kept: a re-crawl adds new revisions to them. Raw events age out on their own.
    """
    _, source = await _crawl_source(session, project_path, source_key)
    units = await session.execute(
        select(IngestUnit.id).where(IngestUnit.source_id == source.id).limit(1)
    )
    preview = {"source_key": source.source_key, "has_units": units.first() is not None}
    if not confirm:
        return {"action": "preview", **preview, "hint": "call again with confirm=true"}
    u = await session.execute(delete(IngestUnit).where(IngestUnit.source_id == source.id))
    w = await session.execute(delete(IngestWatermark).where(IngestWatermark.source_id == source.id))
    await session.flush()
    return {"action": "reset", "source_key": source.source_key,
            "units_deleted": u.rowcount or 0, "watermarks_deleted": w.rowcount or 0}
