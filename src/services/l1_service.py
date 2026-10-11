"""L1 · project overview, notes (incl. policies) and sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import Namespace, Note, Project, Source, SourceType
from src.services.common import (
    DomainError,
    NotFoundError,
    check_length,
    clean_offset,
    iso,
    list_limit,
    now_utc,
    require_text,
)
from src.services.sanitize import sanitize_and_truncate
from src.services.secret_redactor import redact_known_secrets, redact_mapping

USER_SESSION_KEY = "user_session"


def normalize_project_path(project_path: str) -> str:
    path = require_text(project_path, field="project_path")
    return path.rstrip("/") or "/"


async def find_project(session: AsyncSession, project_path: str) -> Project | None:
    result = await session.execute(
        select(Project).where(Project.project_path == normalize_project_path(project_path))
    )
    return result.scalar_one_or_none()


async def require_project(session: AsyncSession, project_path: str) -> Project:
    project = await find_project(session, project_path)
    if project is None:
        raise NotFoundError("project", project_path=normalize_project_path(project_path))
    return project


async def get_or_create_project(session: AsyncSession, project_path: str) -> Project:
    project = await find_project(session, project_path)
    if project is not None:
        return project
    project = Project(project_path=normalize_project_path(project_path))
    session.add(project)
    await session.flush()
    await ensure_user_session_source(session, project)
    return project


async def ensure_user_session_source(session: AsyncSession, project: Project) -> Source:
    """Every project has a 'user_session' source for decisions made live in chat."""
    result = await session.execute(
        select(Source).where(Source.project_id == project.id, Source.source_key == USER_SESSION_KEY)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = Source(
            project_id=project.id,
            source_key=USER_SESSION_KEY,
            source_type=SourceType.user_session,
            display_name="Live agent session",
        )
        session.add(row)
        await session.flush()
    return row


# ── overview ────────────────────────────────────────────────────────────────


async def set_overview(
    session: AsyncSession,
    project_path: str,
    *,
    overview: str | None = None,
    display_name: str | None = None,
) -> dict:
    project = await get_or_create_project(session, project_path)
    action = "created" if project.overview is None and project.display_name is None else "updated"
    if overview is not None:
        project.overview = overview
    if display_name is not None:
        project.display_name = display_name
    project.updated_at = now_utc()
    await session.flush()
    return {"action": action, **_project_to_dict(project)}


async def get_overview(session: AsyncSession, project_path: str) -> dict:
    """Everything a session starts with, in one call: what the project is, its binding
    policies and its key map."""
    project = await require_project(session, project_path)

    async def count(model: Any, *where: Any) -> int:
        stmt = select(func.count()).select_from(model).where(model.project_id == project.id, *where)
        return int((await session.execute(stmt)).scalar_one())

    policies = await list_notes(session, project_path, policy_only=True)
    ns_limit = get_settings().overview_namespace_limit
    namespaces = (
        await session.execute(
            select(Namespace)
            .where(Namespace.project_id == project.id)
            .order_by(Namespace.namespace)
            .limit(ns_limit)
        )
    ).scalars().all()
    counts = {
        "policies": await count(Note, Note.is_policy.is_(True)),
        "notes": await count(Note, Note.is_policy.is_(False)),
        "sources": await count(Source, Source.is_active.is_(True)),
        "namespaces": await count(Namespace),
    }
    return {
        **_project_to_dict(project),
        "counts": counts,
        "policies": policies["items"],
        "namespaces": [
            {"namespace": ns.namespace, "key_template": ns.key_template,
             "summary": sanitize_and_truncate(ns.summary, 200)}
            for ns in namespaces
        ],
        "next": _overview_hint(counts, len(policies["items"]), ns_limit),
    }


def _overview_hint(counts: dict, policies_shown: int, ns_limit: int) -> str:
    hints = []
    if counts["policies"] > policies_shown:
        hints.append("more policies: get_active_policies(offset=...)")
    if counts["namespaces"] > ns_limit:
        hints.append("more namespaces: list_namespaces(query=...)")
    hints.append("find facts: search_facts, or list_fact_index for a namespace")
    return "; ".join(hints)


def _project_to_dict(project: Project) -> dict:
    return {
        "project_path": project.project_path,
        "display_name": project.display_name,
        "overview": sanitize_and_truncate(project.overview),
        "updated_at": iso(project.updated_at),
    }


# ── notes ───────────────────────────────────────────────────────────────────


class PolicyApprovalRequired(DomainError):
    """A policy change reached the service without the user's approval."""

    status = 403

    def __init__(self, note_key: str) -> None:
        super().__init__(f"changing policy {note_key} needs the user's approval")
        self.note_key = note_key

    def payload(self) -> dict:
        return {"error": "approval_required", "note_key": self.note_key, "message": str(self)}


@dataclass(frozen=True)
class NoteChange:
    """A note write, checked and redacted, before it is applied."""

    note_key: str
    title: str
    content: str
    is_policy: bool
    priority: int
    exists: bool
    was_policy: bool
    unchanged: bool

    @property
    def needs_approval(self) -> bool:
        """Adding, changing or removing a policy is the user's call, never the agent's."""
        return not self.unchanged and (self.is_policy or self.was_policy)


def approval_prompt(project_path: str, change: NoteChange) -> str:
    """What the user reads before approving a policy change."""
    if not change.is_policy:
        what = "Stop treating this note as a binding policy"
    elif change.was_policy:
        what = "Change this binding policy"
    else:
        what = "Add this binding policy"
    return (
        f"{what} for {normalize_project_path(project_path)}?\n"
        f"Agents follow policies in every session of this project.\n\n"
        f"{change.note_key} · {change.title}\n\n{change.content}"
    )


def _note_to_dict(row: Note, *, full: bool = True) -> dict:
    out = {
        "note_key": row.note_key,
        "title": row.title,
        "is_policy": row.is_policy,
        "priority": row.priority,
        "updated_at": iso(row.updated_at),
    }
    if row.is_policy:
        out["approved_at"] = iso(row.approved_at)
        out["approved_via"] = row.approved_via
    if full:
        out["content"] = sanitize_and_truncate(row.content)
    return out


async def _find_note(session: AsyncSession, project: Project | None, key: str) -> Note | None:
    if project is None:
        return None
    result = await session.execute(
        select(Note).where(Note.project_id == project.id, Note.note_key == key)
    )
    return result.scalar_one_or_none()


async def plan_note(
    session: AsyncSession,
    project_path: str,
    *,
    note_key: str,
    title: str,
    content: str,
    is_policy: bool = False,
    priority: int = 0,
) -> NoteChange:
    """Validate and redact a note write without applying it. Read-only."""
    key = require_text(note_key, field="note_key")
    title = redact_known_secrets(require_text(title, field="title"))
    content = check_length(
        redact_known_secrets(require_text(content, field="content")),
        field="content", limit=get_settings().max_text_chars,
    )
    row = await _find_note(session, await find_project(session, project_path), key)
    unchanged = row is not None and (
        row.title, row.content, row.is_policy, row.priority
    ) == (title, content, bool(is_policy), int(priority))
    return NoteChange(
        note_key=key, title=title, content=content, is_policy=bool(is_policy),
        priority=int(priority), exists=row is not None,
        was_policy=bool(row and row.is_policy), unchanged=unchanged,
    )


async def upsert_note(
    session: AsyncSession,
    project_path: str,
    *,
    note_key: str,
    title: str,
    content: str,
    is_policy: bool = False,
    priority: int = 0,
    approved_via: str | None = None,
) -> dict:
    """Save a note. A policy change needs approved_via: the channel through which the user
    approved it (MCP elicitation, or the REST API called by a person)."""
    change = await plan_note(
        session, project_path, note_key=note_key, title=title, content=content,
        is_policy=is_policy, priority=priority,
    )
    project = await get_or_create_project(session, project_path)
    row = await _find_note(session, project, change.note_key)
    if change.unchanged and row is not None:
        return {"action": "unchanged", **_note_to_dict(row)}
    if change.needs_approval and not approved_via:
        raise PolicyApprovalRequired(change.note_key)
    action = "updated"
    if row is None:
        row = Note(project_id=project.id, note_key=change.note_key, title=change.title,
                   content=change.content)
        session.add(row)
        action = "created"
    row.title, row.content = change.title, change.content
    row.is_policy, row.priority = change.is_policy, change.priority
    now = now_utc()
    row.approved_at = now if change.is_policy else None
    row.approved_via = approved_via if change.is_policy else None
    row.updated_at = now
    await session.flush()
    return {"action": action, **_note_to_dict(row)}


async def get_note(session: AsyncSession, project_path: str, note_key: str) -> dict:
    project = await require_project(session, project_path)
    result = await session.execute(
        select(Note).where(Note.project_id == project.id, Note.note_key == note_key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("note", note_key=note_key)
    return _note_to_dict(row)


async def list_notes(
    session: AsyncSession,
    project_path: str,
    *,
    policy_only: bool = False,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    project = await require_project(session, project_path)
    capped, skip = list_limit(limit), clean_offset(offset)
    stmt = select(Note).where(Note.project_id == project.id)
    if policy_only:
        stmt = stmt.where(Note.is_policy.is_(True))
    stmt = stmt.order_by(Note.priority.desc(), Note.note_key).offset(skip).limit(capped)
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "items": [_note_to_dict(r, full=policy_only) for r in rows],
        "limit": capped,
        "offset": skip,
    }


async def delete_note(session: AsyncSession, project_path: str, note_key: str) -> dict:
    project = await require_project(session, project_path)
    result = await session.execute(
        select(Note).where(Note.project_id == project.id, Note.note_key == note_key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("note", note_key=note_key)
    await session.delete(row)
    await session.flush()
    return {"action": "deleted", "note_key": note_key}


# ── sources ─────────────────────────────────────────────────────────────────


def _source_to_dict(row: Source) -> dict:
    return {
        "source_key": row.source_key,
        "source_type": row.source_type.value,
        "display_name": row.display_name,
        "url": row.url,
        "connection_config": row.connection_config,
        "read_recipe": sanitize_and_truncate(row.read_recipe) if row.read_recipe else None,
        "is_active": row.is_active,
        "updated_at": iso(row.updated_at),
    }


async def upsert_source(
    session: AsyncSession,
    project_path: str,
    *,
    source_key: str,
    source_type: str,
    display_name: str | None = None,
    url: str | None = None,
    connection_config: dict | None = None,
    read_recipe: str | None = None,
    is_active: bool = True,
) -> dict:
    project = await get_or_create_project(session, project_path)
    key = require_text(source_key, field="source_key")
    try:
        kind = SourceType(source_type)
    except ValueError as exc:
        allowed = [m.value for m in SourceType]
        raise ValueError(f"source_type must be one of {allowed}") from exc
    result = await session.execute(
        select(Source).where(Source.project_id == project.id, Source.source_key == key)
    )
    row = result.scalar_one_or_none()
    action = "updated"
    if row is None:
        row = Source(project_id=project.id, source_key=key, source_type=kind)
        session.add(row)
        action = "created"
    row.source_type = kind
    row.display_name = display_name
    row.url = redact_known_secrets(url)
    # Credentials belong in the agent's own tool config, never in shared memory.
    row.connection_config = redact_mapping(connection_config)
    row.read_recipe = redact_known_secrets(read_recipe)
    row.is_active = bool(is_active)
    row.updated_at = now_utc()
    await session.flush()
    return {"action": action, **_source_to_dict(row)}


async def list_sources(
    session: AsyncSession,
    project_path: str,
    *,
    active_only: bool = True,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    project = await require_project(session, project_path)
    capped, skip = list_limit(limit), clean_offset(offset)
    stmt = select(Source).where(Source.project_id == project.id)
    if active_only:
        stmt = stmt.where(Source.is_active.is_(True))
    stmt = stmt.order_by(Source.source_key).offset(skip).limit(capped)
    rows = (await session.execute(stmt)).scalars().all()
    return {"items": [_source_to_dict(r) for r in rows], "limit": capped, "offset": skip}


async def resolve_source(
    session: AsyncSession, project: Project, source_key: str | None, *, active_only: bool = True
) -> Source:
    """Look up a source by key. An empty key means the built-in user_session source."""
    key = (source_key or "").strip() or USER_SESSION_KEY
    if key == USER_SESSION_KEY:
        return await ensure_user_session_source(session, project)
    stmt = select(Source).where(Source.project_id == project.id, Source.source_key == key)
    if active_only:
        stmt = stmt.where(Source.is_active.is_(True))
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError("source", source_key=key)
    return row
