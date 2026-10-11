"""REST · L1 project overview, notes, sources. project_path is a query parameter."""

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.routers.common import run
from src.services import l1_service

router = APIRouter(prefix="/l1", tags=["L1"])
ProjectPath = Query(..., description="Absolute path of the project repo")


class OverviewBody(BaseModel):
    overview: str | None = None
    display_name: str | None = None


class NoteBody(BaseModel):
    title: str
    content: str
    is_policy: bool = False
    priority: int = 0


class SourceBody(BaseModel):
    source_type: str
    display_name: str | None = None
    url: str | None = None
    connection_config: dict[str, Any] | None = None
    read_recipe: str | None = None
    is_active: bool = True


@router.get("/overview")
async def get_overview(project_path: str = ProjectPath) -> dict:
    return await run(lambda s: l1_service.get_overview(s, project_path))


@router.put("/overview")
async def put_overview(body: OverviewBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: l1_service.set_overview(s, project_path, **body.model_dump()))


@router.get("/notes")
async def list_notes(project_path: str = ProjectPath, policy_only: bool = False,
                     limit: int = 5, offset: int = 0) -> dict:
    return await run(lambda s: l1_service.list_notes(
        s, project_path, policy_only=policy_only, limit=limit, offset=offset))


@router.get("/notes/{note_key}")
async def get_note(note_key: str, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: l1_service.get_note(s, project_path, note_key))


@router.put("/notes/{note_key}")
async def put_note(note_key: str, body: NoteBody, project_path: str = ProjectPath) -> dict:
    """A person holding the API key: a policy saved here counts as approved by them."""
    return await run(lambda s: l1_service.upsert_note(
        s, project_path, note_key=note_key, **body.model_dump(), approved_via="rest"))


@router.delete("/notes/{note_key}")
async def delete_note(note_key: str, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: l1_service.delete_note(s, project_path, note_key))


@router.get("/sources")
async def list_sources(project_path: str = ProjectPath, active_only: bool = True,
                       limit: int = 5, offset: int = 0) -> dict:
    return await run(lambda s: l1_service.list_sources(
        s, project_path, active_only=active_only, limit=limit, offset=offset))


@router.put("/sources/{source_key}")
async def put_source(source_key: str, body: SourceBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: l1_service.upsert_source(
        s, project_path, source_key=source_key, **body.model_dump()))
