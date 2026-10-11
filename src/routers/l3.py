"""REST · L3 facts, history, links and search."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.routers.common import run
from src.services import fact_service, search_service
from src.services.embedding import semantic_enabled

router = APIRouter(prefix="/l3", tags=["L3"])
ProjectPath = Query(..., description="Absolute path of the project repo")


class WriteFactBody(BaseModel):
    change_type: str
    base_revision: int = 0
    title: str
    summary: str
    content: str
    reason: str
    sources: list[dict[str, Any]] | None = None
    kind: str | None = None
    status: str | None = None
    attrs: dict[str, Any] | None = None


class NewFactBody(WriteFactBody):
    """The key as namespace + parts, or as fact_key."""

    namespace: str | None = None
    parts: dict[str, str] | None = None
    fact_key: str | None = None


class BatchBody(BaseModel):
    items: list[dict[str, Any]]


class LinkBody(BaseModel):
    related_fact_key: str
    relation: str = "related"


@router.get("/facts/{fact_key}")
async def get_fact(fact_key: str, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: fact_service.get_fact(s, project_path, fact_key))


@router.post("/facts/{fact_key}/revisions")
async def write_fact(fact_key: str, body: WriteFactBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: fact_service.write_fact(
        s, project_path, fact_key=fact_key, **body.model_dump()))


@router.post("/facts")
async def write_fact_by_namespace(body: NewFactBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: fact_service.write_fact(s, project_path, **body.model_dump()))


@router.post("/facts/batch")
async def write_facts(body: BatchBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: fact_service.write_facts(s, project_path, items=body.items))


@router.get("/facts/{fact_key}/history")
async def history(fact_key: str, project_path: str = ProjectPath, limit: int = 5,
                  offset: int = 0) -> dict:
    return await run(lambda s: fact_service.get_fact_history(
        s, project_path, fact_key, limit=limit, offset=offset))


@router.post("/facts/{fact_key}/links")
async def link(fact_key: str, body: LinkBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: fact_service.link_facts(
        s, project_path, fact_key=fact_key, **body.model_dump()))


@router.get("/search")
async def search(query: str, project_path: str = ProjectPath, kind: str | None = None,
                 limit: int = 5) -> dict:
    return await run(lambda s: search_service.search_facts(
        s, project_path, query, kind=kind, limit=limit))


@router.get("/semantic-search")
async def semantic_search(query: str, project_path: str = ProjectPath, kind: str | None = None,
                          limit: int = 5) -> dict:
    if not semantic_enabled():
        raise HTTPException(404, {"error": "not_found", "what": "semantic_search",
                                  "message": "SEMANTIC_SEARCH_ENABLED=false"})
    return await run(lambda s: search_service.semantic_search(
        s, project_path, query, kind=kind, limit=limit))
