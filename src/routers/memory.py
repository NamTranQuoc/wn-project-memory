from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.services import distillation_service, memory_service, search_service, sql_service

router = APIRouter(prefix="/projects", tags=["memory"])


def _path(project_path: str) -> str:
    return unquote(project_path)


class InitRequest(BaseModel):
    initial_context: str = Field(..., min_length=1)


class EventRequest(BaseModel):
    event_type: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    source_hash: str | None = None


class SqlRequest(BaseModel):
    sql_query: str = Field(..., min_length=1)


class WorkingMemoryRequest(BaseModel):
    current_focus_text: str = Field(..., min_length=1)


class UpsertRuleRequest(BaseModel):
    entity_key: str
    content: str
    raw_event_id: str | None = None
    source_hash: str


@router.post("/{project_path:path}/init")
async def init_project(
    project_path: str,
    body: InitRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await memory_service.init_project_memory(
        session, _path(project_path), body.initial_context
    )


@router.post("/{project_path:path}/events")
async def create_event(
    project_path: str,
    body: EventRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await memory_service.log_raw_event(
        session,
        project_path=_path(project_path),
        event_type=body.event_type,
        content=body.content,
        source_hash=body.source_hash,
    )
    # Trigger distillation best-effort; scheduler retries on failure
    try:
        await distillation_service.distill_event_by_id(session, result["id"])
    except Exception:
        pass
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
