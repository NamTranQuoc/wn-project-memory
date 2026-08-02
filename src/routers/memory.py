from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.services import (
    fact_service,
    l1_reference_service,
    memory_service,
    search_service,
    source_service,
    sql_service,
    task_service,
    watched_ref_service,
    watermark_service,
)

router = APIRouter(prefix="/projects", tags=["memory"])


def _path(project_path: str) -> str:
    return unquote(project_path)


class InitSourceItem(BaseModel):
    source_key: str
    source_type: str
    display_name: str | None = None
    connection_config: dict[str, Any] | None = None
    read_recipe: str | None = None


class InitRequest(BaseModel):
    initial_context: str = Field(..., min_length=1)
    sources: list[InitSourceItem] | None = None


class EventRequest(BaseModel):
    event_type: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    source_hash: str | None = None
    source_key: str | None = None


class SqlRequest(BaseModel):
    sql_query: str = Field(..., min_length=1)


class WorkingMemoryRequest(BaseModel):
    current_focus_text: str = Field(..., min_length=1)


class UpsertRuleRequest(BaseModel):
    entity_key: str
    content: str
    raw_event_id: str | None = None
    source_hash: str


class RegisterSourceRequest(BaseModel):
    source_key: str
    source_type: str
    display_name: str | None = None
    connection_config: dict[str, Any] | None = None
    read_recipe: str | None = None


class WatermarkRequest(BaseModel):
    source_key: str
    stream_key: str | None = None
    indexed_through: dict[str, Any] | None = None
    full_read_ids: list[Any] | None = None
    known_gaps: list[Any] | None = None
    checked_at: datetime | None = None


class FactRequest(BaseModel):
    kind: str
    title: str
    content: str
    priority: int = 0
    status: str | None = None
    occurred_at: datetime | None = None
    source_key: str | None = None


class TaskRequest(BaseModel):
    task_key: str
    title: str
    content: str
    status: str = "open"
    priority: int = 0
    waiting_on: str | None = None
    source_key: str | None = None


class CloseTaskRequest(BaseModel):
    content: str | None = None


class WatchedRefRequest(BaseModel):
    ref_type: str
    ref_value: str
    why: str | None = None
    status_note: str | None = None
    disposition: str = "queued"
    source_key: str | None = None


class L1ReferenceRequest(BaseModel):
    ref_key: str
    title: str
    content: str
    is_policy: bool = False
    priority: int = 0
    source_key: str | None = None
    raw_event_id: str | None = None


@router.post("/{project_path:path}/init")
async def init_project(
    project_path: str,
    body: InitRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    sources = [s.model_dump() for s in body.sources] if body.sources else None
    return await memory_service.init_project_memory(
        session, _path(project_path), body.initial_context, sources=sources
    )


@router.post("/{project_path:path}/events")
async def create_event(
    project_path: str,
    body: EventRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await memory_service.log_raw_event(
        session,
        project_path=_path(project_path),
        event_type=body.event_type,
        content=body.content,
        source_hash=body.source_hash,
        source_key=body.source_key,
    )


@router.post("/{project_path:path}/l1-references")
async def create_l1_reference(
    project_path: str,
    body: L1ReferenceRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await l1_reference_service.upsert_l1_reference(
            session,
            _path(project_path),
            ref_key=body.ref_key,
            title=body.title,
            content=body.content,
            is_policy=body.is_policy,
            priority=body.priority,
            source_key=body.source_key,
            raw_event_id=body.raw_event_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{project_path:path}/l1-references")
async def list_l1_references_route(
    project_path: str,
    policy_only: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await l1_reference_service.list_l1_references(
        session, _path(project_path), policy_only=policy_only
    )


# NOTE: these two suffixed routes (/l1-references/search, /l1-references/policies) must be
# registered BEFORE the generic "/{project_path:path}/search" route below — Starlette matches
# routes in declaration order and its greedy `:path` converter would otherwise let the generic
# route shadow these more specific ones (it happily absorbs ".../l1-references" into
# project_path to satisfy its own literal "/search$" suffix). Same reasoning is why
# "/l1-references/{ref_key}" (a dynamic catch-all) is declared last, after both of these.
@router.get("/{project_path:path}/l1-references/search")
async def search_l1_references_route(
    project_path: str,
    query: str = Query(..., min_length=1),
    search_type: str = Query("hybrid"),
    limit: int = Query(5, ge=1, le=5),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await l1_reference_service.search_l1_references(
            session, _path(project_path), query, search_type=search_type, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{project_path:path}/l1-references/policies")
async def get_active_policies_route(
    project_path: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await l1_reference_service.get_active_policies(session, _path(project_path))


@router.get("/{project_path:path}/l1-references/{ref_key}")
async def get_l1_reference_route(
    project_path: str,
    ref_key: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await l1_reference_service.get_l1_reference(session, _path(project_path), ref_key)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="l1 reference not found")
    return result


@router.get("/{project_path:path}/search")
async def search(
    project_path: str,
    query: str = Query(..., min_length=1),
    search_type: str = Query("hybrid"),
    limit: int = Query(5, ge=1, le=5),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await search_service.search_memory(
        session,
        project_path=_path(project_path),
        query=query,
        search_type=search_type,
        limit=limit,
    )


@router.post("/{project_path:path}/sql")
async def deep_sql(
    project_path: str,
    body: SqlRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await sql_service.query_deep_memory_sql(
            session, project_path=_path(project_path), sql_query=body.sql_query
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{project_path:path}/raw-events/{raw_event_id}")
async def raw_event(
    project_path: str,
    raw_event_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await memory_service.get_raw_context(
        session, project_path=_path(project_path), raw_event_id=raw_event_id
    )
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="raw event not found")
    return result


@router.patch("/{project_path:path}/working-memory")
async def patch_working_memory(
    project_path: str,
    body: WorkingMemoryRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await memory_service.update_working_memory(
        session,
        project_path=_path(project_path),
        current_focus_text=body.current_focus_text,
    )


@router.post("/{project_path:path}/rules")
async def upsert_rule(
    project_path: str,
    body: UpsertRuleRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await memory_service.upsert_distilled_rule(
        session,
        project_path=_path(project_path),
        entity_key=body.entity_key,
        content=body.content,
        raw_event_id=body.raw_event_id,
        source_hash=body.source_hash,
    )


@router.post("/{project_path:path}/sources")
async def register_source(
    project_path: str,
    body: RegisterSourceRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await source_service.register_data_source(
            session,
            _path(project_path),
            source_key=body.source_key,
            source_type=body.source_type,
            display_name=body.display_name,
            connection_config=body.connection_config,
            read_recipe=body.read_recipe,
            added_via="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{project_path:path}/sources")
async def list_sources(
    project_path: str,
    active_only: bool = Query(True),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await source_service.list_data_sources(
        session, _path(project_path), active_only=active_only
    )


@router.put("/{project_path:path}/watermarks")
async def upsert_watermark(
    project_path: str,
    body: WatermarkRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await watermark_service.upsert_watermark(
            session,
            _path(project_path),
            source_key=body.source_key,
            stream_key=body.stream_key,
            indexed_through=body.indexed_through,
            full_read_ids=body.full_read_ids,
            known_gaps=body.known_gaps,
            checked_at=body.checked_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{project_path:path}/watermarks")
async def list_watermarks(
    project_path: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await watermark_service.list_watermarks(session, _path(project_path))


@router.get("/{project_path:path}/watermarks/{source_key}")
async def get_watermark(
    project_path: str,
    source_key: str,
    stream_key: str = Query(""),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await watermark_service.get_watermark(
        session,
        _path(project_path),
        source_key=source_key,
        stream_key=stream_key or None,
    )
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="watermark not found")
    return result


@router.post("/{project_path:path}/facts")
async def create_fact(
    project_path: str,
    body: FactRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await fact_service.upsert_fact(
            session,
            _path(project_path),
            kind=body.kind,
            title=body.title,
            content=body.content,
            priority=body.priority,
            status=body.status,
            occurred_at=body.occurred_at,
            source_key=body.source_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{project_path:path}/facts/search")
async def search_facts_route(
    project_path: str,
    query: str = Query(..., min_length=1),
    search_type: str = Query("hybrid"),
    kind: str | None = Query(None),
    order_by: str = Query("relevance"),
    limit: int = Query(5, ge=1, le=5),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await fact_service.search_facts(
            session,
            _path(project_path),
            query,
            search_type=search_type,
            kind=kind,
            order_by=order_by,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{project_path:path}/tasks")
async def create_task(
    project_path: str,
    body: TaskRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await task_service.upsert_task(
            session,
            _path(project_path),
            task_key=body.task_key,
            title=body.title,
            content=body.content,
            status=body.status,
            priority=body.priority,
            waiting_on=body.waiting_on,
            source_key=body.source_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{project_path:path}/tasks/{task_key}/close")
async def close_task_route(
    project_path: str,
    task_key: str,
    body: CloseTaskRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await task_service.close_task(
        session,
        _path(project_path),
        task_key,
        content=body.content if body else None,
    )
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return result


@router.get("/{project_path:path}/tasks")
async def list_tasks_route(
    project_path: str,
    status: str | None = Query(None),
    order_by: str = Query("priority"),
    limit: int = Query(5, ge=1, le=5),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await task_service.list_tasks(
            session,
            _path(project_path),
            status=status,
            order_by=order_by,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{project_path:path}/watched-refs")
async def create_watched_ref(
    project_path: str,
    body: WatchedRefRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await watched_ref_service.upsert_watched_ref(
            session,
            _path(project_path),
            ref_type=body.ref_type,
            ref_value=body.ref_value,
            why=body.why,
            status_note=body.status_note,
            disposition=body.disposition,
            source_key=body.source_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{project_path:path}/watched-refs")
async def list_watched_refs_route(
    project_path: str,
    disposition: str | None = Query(None),
    limit: int = Query(5, ge=1, le=5),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await watched_ref_service.list_watched_refs(
            session,
            _path(project_path),
            disposition=disposition,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
