"""L3 · facts with an ordered revision history.

The service never merges text. The agent reads the current value, merges the old
value with the new truth, and writes the result with the revision it read
(base_revision). A stale base_revision is refused so no update is lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import (
    ChangeType,
    Fact,
    FactKind,
    FactLink,
    Namespace,
    Project,
    Revision,
    RevisionSource,
    Source,
)
from src.services.common import (
    DomainError,
    NotFoundError,
    check_length,
    clean_offset,
    error_payload,
    iso,
    list_limit,
    now_utc,
    parse_uuid,
    require_text,
)
from src.services.embedding import maybe_embed
from src.services.l1_service import get_or_create_project, require_project, resolve_source
from src.services.namespace_service import build_key, get_namespace, validate_fact_key
from src.services.raw_service import touch_raw_event
from src.services.sanitize import sanitize_and_truncate
from src.services.secret_redactor import redact_known_secrets, redact_mapping

SUMMARY_MAX = 280
LINK_RELATIONS = frozenset({"replaces", "depends_on", "related"})


class RevisionConflict(DomainError):
    """Someone wrote first: the agent reads `fact`, merges again and retries."""

    status = 409

    def __init__(self, current: int, fact: dict | None) -> None:
        super().__init__(f"base_revision is stale; current revision is {current}")
        self.current = current
        self.fact = fact

    def payload(self) -> dict:
        return {"error": "revision_conflict", "message": str(self),
                "current_revision": self.current, "fact": self.fact}


@dataclass(frozen=True)
class SourceRef:
    source_key: str | None = None
    source_url: str | None = None
    raw_event_id: str | None = None
    source_hash: str | None = None

    @classmethod
    def parse(cls, raw: Any) -> SourceRef:
        if not isinstance(raw, dict):
            raise ValueError("each source must be an object")
        return cls(
            source_key=raw.get("source_key") or None,
            source_url=redact_known_secrets(raw.get("source_url") or None),
            raw_event_id=raw.get("raw_event_id") or None,
            source_hash=raw.get("source_hash") or None,
        )


def fact_to_dict(fact: Fact, namespace: str | None = None) -> dict:
    out = {
        "fact_key": fact.fact_key,
        "kind": fact.kind.value,
        "status": fact.status,
        "title": fact.title,
        "summary": fact.summary,
        "content": sanitize_and_truncate(fact.content),
        "attrs": fact.attrs,
        "revision": fact.revision,
        "updated_at": iso(fact.updated_at),
    }
    if namespace:
        out["namespace"] = namespace
    return out


async def _lock_fact(session: AsyncSession, project_id, fact_key: str) -> Fact | None:
    result = await session.execute(
        select(Fact)
        .where(Fact.project_id == project_id, Fact.fact_key == fact_key)
        .with_for_update()
    )
    return result.scalar_one_or_none()


def _same_value(fact: Fact, title: str, summary: str, content: str, status, attrs) -> bool:
    return (
        fact.title == title
        and fact.summary == summary
        and fact.content == content
        and fact.status == status
        and (fact.attrs or None) == (attrs or None)
    )


async def _resolve_key(
    session: AsyncSession,
    project: Project,
    fact_key: str | None,
    namespace: str | None,
    parts: dict[str, str] | None,
) -> str:
    """The key comes either as is, or from a namespace template and its parts."""
    if fact_key and namespace:
        raise ValueError("pass fact_key, or namespace with parts, not both")
    if namespace:
        ns = await get_namespace(session, project, namespace)
        return build_key(ns.namespace, ns.key_template, parts or {})
    return require_text(fact_key, field="fact_key")


async def write_fact(
    session: AsyncSession,
    project_path: str,
    *,
    change_type: str,
    title: str,
    summary: str,
    content: str,
    reason: str,
    base_revision: int = 0,
    fact_key: str | None = None,
    namespace: str | None = None,
    parts: dict[str, str] | None = None,
    sources: list[Any] | None = None,
    kind: str | None = None,
    status: str | None = None,
    attrs: dict | None = None,
) -> dict:
    """Write the full new value of one fact. A new fact is change_type=create with
    base_revision 0; if the key already exists, RevisionConflict returns its value."""
    project = await get_or_create_project(session, project_path)
    key = await _resolve_key(session, project, fact_key, namespace, parts)
    ns = await validate_fact_key(session, project, key)
    try:
        change = ChangeType(change_type)
    except ValueError as exc:
        raise ValueError(f"change_type must be one of {[c.value for c in ChangeType]}") from exc
    # Second layer behind the agent rule "never send secrets": stored values are redacted,
    # and the unchanged check below compares redacted with redacted.
    title = redact_known_secrets(require_text(title, field="title"))
    summary = redact_known_secrets(require_text(summary, field="summary"))
    if len(summary) > SUMMARY_MAX:
        raise ValueError(f"summary must be at most {SUMMARY_MAX} characters")
    content = redact_known_secrets(require_text(content, field="content"))
    check_length(content, field="content", limit=get_settings().max_text_chars)
    reason = redact_known_secrets(require_text(reason, field="reason"))
    attrs = redact_mapping(attrs, redact_known_secrets) if attrs else attrs
    refs = [SourceRef.parse(s) for s in (sources or [])]

    fact = await _lock_fact(session, project.id, key)
    current = fact.revision if fact else 0
    if int(base_revision) != current:
        raise RevisionConflict(current, fact_to_dict(fact, ns.namespace) if fact else None)
    if fact is None and change is not ChangeType.create:
        raise ValueError("a new fact needs change_type 'create' and base_revision 0")
    if fact is not None and change is ChangeType.create:
        raise ValueError("fact exists; use replace, merge, close or reopen")
    if fact is not None and _same_value(fact, title, summary, content, status, attrs):
        return {"action": "unchanged", **fact_to_dict(fact, ns.namespace)}

    resolved: list[tuple[SourceRef, Source]] = []
    for ref in refs:
        resolved.append((ref, await resolve_source(session, project, ref.source_key)))

    now = now_utc()
    if fact is None:
        try:
            fact_kind = FactKind(kind) if kind else ns.default_kind
        except ValueError as exc:
            raise ValueError(f"kind must be one of {[k.value for k in FactKind]}") from exc
        fact = Fact(
            project_id=project.id,
            namespace_id=ns.id,
            fact_key=key,
            kind=fact_kind,
            created_at=now,
            revision=0,
            title=title,
            summary=summary,
            content=content,
        )
        session.add(fact)
    elif kind:
        fact.kind = FactKind(kind)
    fact.revision = current + 1
    fact.title, fact.summary, fact.content = title, summary, content
    fact.status, fact.attrs = status, attrs
    fact.updated_at = now
    fact.embedding = await maybe_embed(f"{title}\n{summary}\n{content}")
    await session.flush()

    rev = Revision(
        fact_id=fact.id,
        revision=fact.revision,
        change_type=change,
        reason=reason,
        title=title,
        summary=summary,
        content=content,
        status=status,
        attrs=attrs,
        changed_at=now,
    )
    session.add(rev)
    await session.flush()
    for ref, source in resolved:
        raw_id = parse_uuid(ref.raw_event_id, field="raw_event_id")
        session.add(
            RevisionSource(
                revision_id=rev.id,
                source_id=source.id,
                source_url=ref.source_url,
                raw_event_id=raw_id,
                source_hash=ref.source_hash,
            )
        )
        if raw_id is not None:
            await touch_raw_event(session, project.id, raw_id)
    await session.flush()
    return {"action": change.value, **fact_to_dict(fact, ns.namespace)}


MAX_BATCH = 10
_WRITE_FIELDS = frozenset({
    "change_type", "title", "summary", "content", "reason", "base_revision", "fact_key",
    "namespace", "parts", "sources", "kind", "status", "attrs",
})


async def write_facts(session: AsyncSession, project_path: str, *, items: list[Any]) -> dict:
    """Write up to MAX_BATCH facts. Each item stands alone: a refused item (conflict, key
    rule, invalid field) is reported and rolled back without touching the others."""
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")
    if len(items) > MAX_BATCH:
        raise ValueError(f"at most {MAX_BATCH} facts per call")
    results: list[dict] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) - _WRITE_FIELDS:
            unknown = sorted(set(item) - _WRITE_FIELDS) if isinstance(item, dict) else []
            results.append({"index": index, "error": "invalid",
                            "message": f"each item is a write_fact call; unknown fields {unknown}"})
            continue
        try:
            async with session.begin_nested():
                results.append({"index": index, **await write_fact(session, project_path, **item)})
        except (DomainError, ValueError, TypeError) as exc:
            results.append({"index": index, **error_payload(exc)})
    written = sum(1 for r in results if "error" not in r)
    return {"count": len(results), "written": written, "items": results}


async def _get_fact_row(session: AsyncSession, project_path: str, fact_key: str) -> tuple[Fact, str]:
    project = await require_project(session, project_path)
    result = await session.execute(
        select(Fact, Namespace.namespace)
        .join(Namespace, Namespace.id == Fact.namespace_id)
        .where(Fact.project_id == project.id, Fact.fact_key == fact_key)
    )
    found = result.first()
    if found is None:
        raise NotFoundError("fact", fact_key=fact_key)
    return found[0], found[1]


async def _sources_of(session: AsyncSession, revision_ids: list) -> dict:
    if not revision_ids:
        return {}
    result = await session.execute(
        select(RevisionSource, Source.source_key)
        .join(Source, Source.id == RevisionSource.source_id)
        .where(RevisionSource.revision_id.in_(revision_ids))
    )
    out: dict = {}
    for src, source_key in result.all():
        out.setdefault(src.revision_id, []).append(
            {
                "source_key": source_key,
                "source_url": src.source_url,
                "raw_event_id": str(src.raw_event_id) if src.raw_event_id else None,
            }
        )
    return out


async def get_fact(session: AsyncSession, project_path: str, fact_key: str) -> dict:
    fact, namespace = await _get_fact_row(session, project_path, fact_key)
    latest = (
        await session.execute(
            select(Revision).where(Revision.fact_id == fact.id, Revision.revision == fact.revision)
        )
    ).scalar_one_or_none()
    links = await session.execute(
        select(FactLink.relation, Fact.fact_key)
        .join(Fact, Fact.id == FactLink.related_fact_id)
        .where(FactLink.fact_id == fact.id)
        .limit(list_limit(None))
    )
    out = fact_to_dict(fact, namespace)
    if latest is not None:
        sources = await _sources_of(session, [latest.id])
        out["last_change"] = {
            "change_type": latest.change_type.value,
            "reason": latest.reason,
            "changed_at": iso(latest.changed_at),
            "sources": sources.get(latest.id, []),
        }
    out["links"] = [{"relation": rel, "fact_key": key} for rel, key in links.all()]
    return out


async def get_fact_history(
    session: AsyncSession,
    project_path: str,
    fact_key: str,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    fact, namespace = await _get_fact_row(session, project_path, fact_key)
    capped, skip = list_limit(limit), clean_offset(offset)
    revs = (
        await session.execute(
            select(Revision)
            .where(Revision.fact_id == fact.id)
            .order_by(Revision.revision.desc())
            .offset(skip)
            .limit(capped)
        )
    ).scalars().all()
    sources = await _sources_of(session, [r.id for r in revs])
    return {
        "fact_key": fact.fact_key,
        "namespace": namespace,
        "current_revision": fact.revision,
        "items": [
            {
                "revision": r.revision,
                "change_type": r.change_type.value,
                "reason": r.reason,
                "changed_at": iso(r.changed_at),
                "title": r.title,
                "summary": r.summary,
                "content": sanitize_and_truncate(r.content),
                "status": r.status,
                "sources": sources.get(r.id, []),
            }
            for r in revs
        ],
        "limit": capped,
        "offset": skip,
    }


async def link_facts(
    session: AsyncSession,
    project_path: str,
    *,
    fact_key: str,
    related_fact_key: str,
    relation: str = "related",
) -> dict:
    if relation not in LINK_RELATIONS:
        raise ValueError(f"relation must be one of {sorted(LINK_RELATIONS)}")
    fact, _ = await _get_fact_row(session, project_path, fact_key)
    related, _ = await _get_fact_row(session, project_path, related_fact_key)
    if fact.id == related.id:
        raise ValueError("a fact cannot link to itself")
    existing = await session.get(FactLink, (fact.id, related.id, relation))
    if existing is not None:
        return {"action": "unchanged", "fact_key": fact_key, "related_fact_key": related_fact_key,
                "relation": relation}
    session.add(FactLink(fact_id=fact.id, related_fact_id=related.id, relation=relation))
    await session.flush()
    return {"action": "created", "fact_key": fact_key, "related_fact_key": related_fact_key,
            "relation": relation}
