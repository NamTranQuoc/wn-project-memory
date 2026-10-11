"""REST · global people, accounts, teams and notes (no project_path)."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.routers.common import run
from src.services import global_service

router = APIRouter(prefix="/global", tags=["Global"])


class IdentityBody(BaseModel):
    system: str
    handle: str
    url: str | None = None


class PersonBody(BaseModel):
    display_name: str
    address_as: str | None = None
    dm_note: str | None = None
    links: dict[str, Any] | None = None
    identities: list[IdentityBody] = Field(default_factory=list)


class MemberBody(BaseModel):
    person_key: str
    role: str | None = None


class TeamBody(BaseModel):
    name: str
    description: str | None = None
    links: dict[str, Any] | None = None
    members: list[MemberBody] | None = None


class NoteBody(BaseModel):
    title: str
    content: str


@router.get("/people")
async def people(query: str | None = None, system: str | None = None, handle: str | None = None,
                 limit: int = 5, offset: int = 0) -> dict:
    if system and handle:
        return await run(lambda s: global_service.find_person(s, system=system, handle=handle))
    return await run(lambda s: global_service.list_people(s, query=query, limit=limit, offset=offset))


@router.get("/people/{person_key}")
async def get_person(person_key: str) -> dict:
    return await run(lambda s: global_service.find_person(s, person_key=person_key))


@router.put("/people/{person_key}")
async def put_person(person_key: str, body: PersonBody) -> dict:
    data = body.model_dump()
    return await run(lambda s: global_service.upsert_person(s, person_key=person_key, **data))


@router.put("/people/{person_key}/identities")
async def put_identity(person_key: str, body: IdentityBody) -> dict:
    return await run(lambda s: global_service.upsert_identity(
        s, person_key=person_key, **body.model_dump()))


@router.delete("/identities/{system}/{handle}")
async def delete_identity(system: str, handle: str) -> dict:
    return await run(lambda s: global_service.delete_identity(s, system=system, handle=handle))


@router.get("/teams/{team_key}")
async def get_team(team_key: str) -> dict:
    return await run(lambda s: global_service.get_team(s, team_key))


@router.put("/teams/{team_key}")
async def put_team(team_key: str, body: TeamBody) -> dict:
    data = body.model_dump()
    return await run(lambda s: global_service.upsert_team(s, team_key=team_key, **data))


@router.put("/teams/{team_key}/members")
async def put_members(team_key: str, members: list[MemberBody]) -> dict:
    data = [m.model_dump() for m in members]
    return await run(lambda s: global_service.set_team_members(s, team_key=team_key, members=data))


@router.get("/notes")
async def list_notes(query: str | None = None, limit: int = 5, offset: int = 0) -> dict:
    return await run(lambda s: global_service.list_global_notes(
        s, query=query, limit=limit, offset=offset))


@router.get("/notes/{note_key}")
async def get_note(note_key: str) -> dict:
    return await run(lambda s: global_service.get_global_note(s, note_key))


@router.put("/notes/{note_key}")
async def put_note(note_key: str, body: NoteBody) -> dict:
    return await run(lambda s: global_service.upsert_global_note(
        s, note_key=note_key, **body.model_dump()))
