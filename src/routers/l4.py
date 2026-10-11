"""REST · L4 raw events, read-only SQL, ingest ledger and watermarks."""

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.routers.common import run
from src.services import ingest_service, raw_service, sql_service

router = APIRouter(prefix="/l4", tags=["L4"])
ProjectPath = Query(..., description="Absolute path of the project repo")


class EventBody(BaseModel):
    event_type: str
    content: str
    source_key: str | None = None
    source_url: str | None = None
    source_hash: str | None = None


class SqlBody(BaseModel):
    sql_query: str


class IngestBody(BaseModel):
    source_key: str
    content: str
    external_id: str | None = None
    stream_key: str | None = None
    source_hash: str | None = None
    source_url: str | None = None
    event_type: str = "source_unit"
    unit_metadata: dict[str, Any] | None = None


class CheckBody(BaseModel):
    source_key: str
    candidates: list[dict[str, Any]]
    stream_key: str | None = None


class WatermarkBody(BaseModel):
    source_key: str
    stream_key: str | None = None
    indexed_through: dict[str, Any] | None = None
    full_read_ids: list[Any] | None = None
    known_gaps: list[Any] | None = None
    checked_at: str | None = None


class ResetBody(BaseModel):
    source_key: str
    confirm: bool = False


@router.post("/events")
async def log_event(body: EventBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: raw_service.log_raw_event(s, project_path, **body.model_dump()))


@router.get("/events/{raw_event_id}")
async def get_event(raw_event_id: str, project_path: str = ProjectPath, offset: int = 0,
                    max_chars: int = 8000) -> dict:
    return await run(lambda s: raw_service.get_raw_context(
        s, project_path, raw_event_id, offset=offset, max_chars=max_chars))


@router.post("/sql")
async def sql(body: SqlBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: sql_service.query_deep_memory_sql(s, project_path, body.sql_query))


@router.post("/ingest/units")
async def ingest(body: IngestBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: ingest_service.ingest_source_unit(s, project_path, **body.model_dump()))


@router.post("/ingest/check")
async def check(body: CheckBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: ingest_service.check_source_units(s, project_path, **body.model_dump()))


@router.get("/ingest/units")
async def get_unit(source_key: str, project_path: str = ProjectPath, item_key: str | None = None,
                   external_id: str | None = None, stream_key: str | None = None) -> dict:
    return await run(lambda s: ingest_service.get_source_unit(
        s, project_path, source_key=source_key, item_key=item_key, external_id=external_id,
        stream_key=stream_key))


@router.put("/ingest/watermarks")
async def put_watermark(body: WatermarkBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: ingest_service.upsert_watermark(s, project_path, **body.model_dump()))


@router.get("/ingest/watermarks")
async def list_watermarks(project_path: str = ProjectPath, limit: int = 5) -> dict:
    return await run(lambda s: ingest_service.list_watermarks(s, project_path, limit=limit))


@router.get("/ingest/watermarks/{source_key}")
async def get_watermark(source_key: str, project_path: str = ProjectPath,
                        stream_key: str | None = None) -> dict:
    return await run(lambda s: ingest_service.get_watermark(
        s, project_path, source_key=source_key, stream_key=stream_key))


@router.post("/ingest/reset")
async def reset(body: ResetBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: ingest_service.reset_source_ingest(s, project_path, **body.model_dump()))
