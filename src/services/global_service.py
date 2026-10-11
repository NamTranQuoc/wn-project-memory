"""Global · people, accounts, teams and notes. No project_path: shared by every project."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import GlobalNote, Identity, Person, Team, TeamMember
from src.services.common import (
    LIKE_ESCAPE,
    NotFoundError,
    check_length,
    clean_offset,
    contains_pattern,
    iso,
    list_limit,
    now_utc,
    require_text,
)
from src.services.sanitize import sanitize_and_truncate
from src.services.secret_redactor import redact_known_secrets, redact_mapping


def _key(value: str, field: str) -> str:
    return require_text(value, field=field).lower()


def _system(value: str) -> str:
    return require_text(value, field="system").lower()


async def _person_by_key(session: AsyncSession, person_key: str) -> Person:
    row = (
        await session.execute(select(Person).where(Person.person_key == _key(person_key, "person_key")))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("person", person_key=person_key)
    return row


async def _team_by_key(session: AsyncSession, team_key: str) -> Team:
    row = (
        await session.execute(select(Team).where(Team.team_key == _key(team_key, "team_key")))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("team", team_key=team_key)
    return row


async def _person_to_dict(session: AsyncSession, person: Person) -> dict:
    identities = (
        await session.execute(
            select(Identity).where(Identity.person_id == person.id).order_by(Identity.system)
        )
    ).scalars().all()
    teams = (
        await session.execute(
            select(Team.team_key, Team.name, TeamMember.role)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .where(TeamMember.person_id == person.id)
            .order_by(Team.team_key)
        )
    ).all()
    return {
        "person_key": person.person_key,
        "display_name": person.display_name,
        "address_as": person.address_as,
        "dm_note": person.dm_note,
        "links": person.links,
        "identities": [{"system": i.system, "handle": i.handle, "url": i.url} for i in identities],
        "teams": [{"team_key": k, "name": n, "role": r} for k, n, r in teams],
        "updated_at": iso(person.updated_at),
    }


async def find_person(
    session: AsyncSession,
    *,
    person_key: str | None = None,
    system: str | None = None,
    handle: str | None = None,
) -> dict:
    """By person_key, or by an account (system + handle)."""
    if person_key:
        return await _person_to_dict(session, await _person_by_key(session, person_key))
    if not (system and handle):
        raise ValueError("give person_key, or system and handle")
    identity = await session.get(Identity, (_system(system), handle.strip()))
    if identity is None:
        raise NotFoundError("person", system=system, handle=handle)
    person = await session.get(Person, identity.person_id)
    return await _person_to_dict(session, person)


async def list_people(
    session: AsyncSession,
    *,
    query: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    capped, skip = list_limit(limit), clean_offset(offset)
    stmt = select(Person)
    if query and query.strip():
        like = contains_pattern(query.strip())
        stmt = stmt.where(
            or_(
                Person.display_name.ilike(like, escape=LIKE_ESCAPE),
                Person.person_key.ilike(like, escape=LIKE_ESCAPE),
            )
        )
    rows = (
        await session.execute(stmt.order_by(Person.person_key).offset(skip).limit(capped))
    ).scalars().all()
    return {
        "items": [
            {"person_key": p.person_key, "display_name": p.display_name, "address_as": p.address_as}
            for p in rows
        ],
        "limit": capped,
        "offset": skip,
    }


async def upsert_person(
    session: AsyncSession,
    *,
    person_key: str,
    display_name: str,
    address_as: str | None = None,
    dm_note: str | None = None,
    links: dict | None = None,
    identities: list[dict[str, Any]] | None = None,
) -> dict:
    key = _key(person_key, "person_key")
    row = (await session.execute(select(Person).where(Person.person_key == key))).scalar_one_or_none()
    action = "updated"
    if row is None:
        row = Person(person_key=key, display_name=display_name)
        session.add(row)
        action = "created"
    row.display_name = require_text(display_name, field="display_name")
    row.address_as = address_as
    row.dm_note = redact_known_secrets(dm_note)
    row.links = redact_mapping(links, redact_known_secrets)
    row.updated_at = now_utc()
    await session.flush()
    for ident in identities or []:
        await _put_identity(session, row, ident.get("system"), ident.get("handle"), ident.get("url"))
    await session.flush()
    return {"action": action, **(await _person_to_dict(session, row))}


async def _put_identity(
    session: AsyncSession, person: Person, system: str | None, handle: str | None, url: str | None
) -> None:
    sys_name = _system(system or "")
    handle_value = require_text(handle, field="handle")
    existing = await session.get(Identity, (sys_name, handle_value))
    if existing is not None and existing.person_id != person.id:
        raise ValueError(f"{sys_name}:{handle_value} already belongs to another person")
    if existing is None:
        session.add(Identity(system=sys_name, handle=handle_value, person_id=person.id, url=url))
    else:
        existing.url = url


async def upsert_identity(
    session: AsyncSession, *, person_key: str, system: str, handle: str, url: str | None = None
) -> dict:
    person = await _person_by_key(session, person_key)
    await _put_identity(session, person, system, handle, url)
    await session.flush()
    return {"action": "saved", **(await _person_to_dict(session, person))}


async def delete_identity(session: AsyncSession, *, system: str, handle: str) -> dict:
    result = await session.execute(
        delete(Identity).where(Identity.system == _system(system), Identity.handle == handle.strip())
    )
    if not result.rowcount:
        raise NotFoundError("identity", system=system, handle=handle)
    return {"action": "deleted", "system": _system(system), "handle": handle.strip()}


async def _team_to_dict(session: AsyncSession, team: Team) -> dict:
    members = (
        await session.execute(
            select(Person.person_key, Person.display_name, TeamMember.role)
            .join(TeamMember, TeamMember.person_id == Person.id)
            .where(TeamMember.team_id == team.id)
            .order_by(Person.person_key)
        )
    ).all()
    return {
        "team_key": team.team_key,
        "name": team.name,
        "description": team.description,
        "links": team.links,
        "members": [{"person_key": k, "display_name": n, "role": r} for k, n, r in members],
        "updated_at": iso(team.updated_at),
    }


async def get_team(session: AsyncSession, team_key: str) -> dict:
    return await _team_to_dict(session, await _team_by_key(session, team_key))


async def upsert_team(
    session: AsyncSession,
    *,
    team_key: str,
    name: str,
    description: str | None = None,
    links: dict | None = None,
    members: list[dict[str, Any]] | None = None,
) -> dict:
    key = _key(team_key, "team_key")
    row = (await session.execute(select(Team).where(Team.team_key == key))).scalar_one_or_none()
    action = "updated"
    if row is None:
        row = Team(team_key=key, name=name)
        session.add(row)
        action = "created"
    row.name = require_text(name, field="name")
    row.description = redact_known_secrets(description)
    row.links = redact_mapping(links, redact_known_secrets)
    row.updated_at = now_utc()
    await session.flush()
    if members is not None:
        await _replace_members(session, row, members)
    return {"action": action, **(await _team_to_dict(session, row))}


async def _replace_members(session: AsyncSession, team: Team, members: list[dict[str, Any]]) -> None:
    await session.execute(delete(TeamMember).where(TeamMember.team_id == team.id))
    for m in members:
        person = await _person_by_key(session, m.get("person_key") or "")
        session.add(TeamMember(team_id=team.id, person_id=person.id, role=m.get("role")))
    await session.flush()


async def set_team_members(
    session: AsyncSession, *, team_key: str, members: list[dict[str, Any]]
) -> dict:
    """Replace the member list. Each member: {person_key, role?}. People must exist."""
    team = await _team_by_key(session, team_key)
    await _replace_members(session, team, members)
    return {"action": "saved", **(await _team_to_dict(session, team))}


async def upsert_global_note(session: AsyncSession, *, note_key: str, title: str, content: str) -> dict:
    key = _key(note_key, "note_key")
    row = await session.get(GlobalNote, key)
    action = "updated"
    if row is None:
        row = GlobalNote(note_key=key, title=title, content=content)
        session.add(row)
        action = "created"
    row.title = redact_known_secrets(require_text(title, field="title"))
    row.content = check_length(
        redact_known_secrets(require_text(content, field="content")),
        field="content", limit=get_settings().max_text_chars,
    )
    row.updated_at = now_utc()
    await session.flush()
    return {"action": action, "note_key": key, "title": row.title}


async def get_global_note(session: AsyncSession, note_key: str) -> dict:
    row = await session.get(GlobalNote, _key(note_key, "note_key"))
    if row is None:
        raise NotFoundError("global_note", note_key=note_key)
    return {"note_key": row.note_key, "title": row.title,
            "content": sanitize_and_truncate(row.content), "updated_at": iso(row.updated_at)}


async def list_global_notes(
    session: AsyncSession, *, query: str | None = None, limit: int | None = None,
    offset: int | None = None,
) -> dict:
    capped, skip = list_limit(limit), clean_offset(offset)
    stmt = select(GlobalNote)
    if query and query.strip():
        like = contains_pattern(query.strip())
        stmt = stmt.where(
            or_(
                GlobalNote.title.ilike(like, escape=LIKE_ESCAPE),
                GlobalNote.note_key.ilike(like, escape=LIKE_ESCAPE),
            )
        )
    rows = (
        await session.execute(stmt.order_by(GlobalNote.note_key).offset(skip).limit(capped))
    ).scalars().all()
    return {"items": [{"note_key": r.note_key, "title": r.title} for r in rows],
            "limit": capped, "offset": skip}
