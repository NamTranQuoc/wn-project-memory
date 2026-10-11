"""L2 · namespaces and the fact-key rule.

A namespace owns a key template such as 'decision.api.{service}.{topic}'. Every L3
fact key must match exactly one template, so the read path and the write path build
the same key from the same parts.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Fact, FactKind, Namespace, Project
from src.services.common import (
    DomainError,
    NotFoundError,
    clean_offset,
    iso,
    list_limit,
    now_utc,
    require_text,
)
from src.services.l1_service import get_or_create_project, require_project
from src.services.text_search import namespace_matches, search_terms

_SEGMENT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SLOT_RE = re.compile(r"^\{([a-z_][a-z0-9_]*)\}$")


class KeyRuleError(DomainError, ValueError):
    """A fact key or template breaks the namespace rule."""

    def __init__(self, message: str, **detail: object) -> None:
        super().__init__(message)
        self.detail = detail

    def payload(self) -> dict:
        return {"error": "key_rule", "message": str(self), **self.detail}


def slugify(value: str) -> str:
    """'Idempotency Key' → 'idempotency-key'; Vietnamese marks are folded ('Đăng nhập' → 'dang-nhap')."""
    text = str(value).replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-{2,}", "-", text)


def validate_namespace_name(namespace: str) -> str:
    name = require_text(namespace, field="namespace").lower()
    if not all(_SEGMENT_RE.match(seg) for seg in name.split(".")):
        raise KeyRuleError(
            "namespace segments use a-z, 0-9 and '-', separated by '.'", namespace=name
        )
    return name


def parse_template(namespace: str, key_template: str) -> list[str | None]:
    """Return the segments after the namespace: a literal string, or None for a slot.

    Slot names are returned separately by slot_names().
    """
    template = require_text(key_template, field="key_template")
    prefix = namespace + "."
    if not template.startswith(prefix):
        raise KeyRuleError("key_template must start with the namespace and a '.'",
                           namespace=namespace, key_template=template)
    tail = template[len(prefix):].split(".")
    segments: list[str | None] = []
    for seg in tail:
        if _SLOT_RE.match(seg):
            segments.append(None)
        elif _SEGMENT_RE.match(seg):
            segments.append(seg)
        else:
            raise KeyRuleError(f"bad template segment '{seg}'", key_template=template)
    if None not in segments:
        raise KeyRuleError("key_template needs at least one {slot}", key_template=template)
    return segments


def slot_names(key_template: str) -> list[str]:
    return re.findall(r"\{([a-z_][a-z0-9_]*)\}", key_template)


def build_key(namespace: str, key_template: str, parts: dict[str, str]) -> str:
    segments = parse_template(namespace, key_template)
    names = iter(slot_names(key_template))
    out = [namespace]
    missing: list[str] = []
    for seg in segments:
        if seg is not None:
            out.append(seg)
            continue
        name = next(names)
        slug = slugify(parts.get(name, "") or "")
        if not slug:
            missing.append(name)
        out.append(slug)
    if missing:
        raise KeyRuleError(f"missing parts: {missing}", key_template=key_template)
    return ".".join(out)


def key_matches(namespace: str, key_template: str, key: str) -> bool:
    if not key.startswith(namespace + "."):
        return False
    segments = parse_template(namespace, key_template)
    tail = key[len(namespace) + 1:].split(".")
    if len(tail) != len(segments):
        return False
    for seg, part in zip(segments, tail):
        if seg is None:
            if not _SEGMENT_RE.match(part):
                return False
        elif seg != part:
            return False
    return True


# ── persistence ─────────────────────────────────────────────────────────────


def _ns_to_dict(row: Namespace, *, fact_count: int | None = None) -> dict:
    out = {
        "namespace": row.namespace,
        "key_template": row.key_template,
        "default_kind": row.default_kind.value,
        "summary": row.summary,
        "keywords": list(row.keywords or []),
        "updated_at": iso(row.updated_at),
    }
    if fact_count is not None:
        out["fact_count"] = fact_count
    return out


async def upsert_namespace(
    session: AsyncSession,
    project_path: str,
    *,
    namespace: str,
    key_template: str,
    summary: str,
    default_kind: str = "fact",
    keywords: list[str] | None = None,
) -> dict:
    project = await get_or_create_project(session, project_path)
    name = validate_namespace_name(namespace)
    parse_template(name, key_template)
    try:
        kind = FactKind(default_kind)
    except ValueError as exc:
        raise ValueError(f"default_kind must be one of {[k.value for k in FactKind]}") from exc
    result = await session.execute(
        select(Namespace).where(Namespace.project_id == project.id, Namespace.namespace == name)
    )
    row = result.scalar_one_or_none()
    action = "updated"
    if row is None:
        row = Namespace(project_id=project.id, namespace=name)
        session.add(row)
        action = "created"
    elif row.key_template != key_template.strip():
        used = await session.execute(
            select(func.count()).select_from(Fact).where(Fact.namespace_id == row.id)
        )
        if used.scalar_one():
            raise KeyRuleError(
                "key_template cannot change while facts use it; create a new namespace",
                namespace=name,
            )
    row.key_template = key_template.strip()
    row.summary = require_text(summary, field="summary")
    row.default_kind = kind
    row.keywords = sorted({k.strip().lower() for k in (keywords or []) if k and k.strip()})
    row.updated_at = now_utc()
    await session.flush()
    return {"action": action, **_ns_to_dict(row)}


async def list_namespaces(
    session: AsyncSession,
    project_path: str,
    *,
    query: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    project = await require_project(session, project_path)
    capped, skip = list_limit(limit), clean_offset(offset)
    fact_count = (
        select(func.count())
        .select_from(Fact)
        .where(Fact.namespace_id == Namespace.id)
        .scalar_subquery()
    )
    stmt = select(Namespace, fact_count).where(Namespace.project_id == project.id)
    if query and query.strip():
        stmt = stmt.where(namespace_matches(search_terms(query)))
    stmt = stmt.order_by(Namespace.namespace).offset(skip).limit(capped)
    rows = (await session.execute(stmt)).all()
    return {
        "items": [_ns_to_dict(ns, fact_count=int(n)) for ns, n in rows],
        "limit": capped,
        "offset": skip,
    }


async def get_namespace(session: AsyncSession, project: Project, namespace: str) -> Namespace:
    result = await session.execute(
        select(Namespace).where(
            Namespace.project_id == project.id, Namespace.namespace == namespace.strip().lower()
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("namespace", namespace=namespace)
    return row


async def build_fact_key(
    session: AsyncSession, project_path: str, *, namespace: str, parts: dict[str, str]
) -> dict:
    project = await require_project(session, project_path)
    ns = await get_namespace(session, project, namespace)
    key = build_key(ns.namespace, ns.key_template, parts)
    return {"fact_key": key, "namespace": ns.namespace, "key_template": ns.key_template}


async def validate_fact_key(session: AsyncSession, project: Project, key: str) -> Namespace:
    """Find the namespace whose template the key matches. Longest namespace wins."""
    fact_key = require_text(key, field="fact_key")
    result = await session.execute(
        select(Namespace)
        .where(Namespace.project_id == project.id)
        .where(literal(fact_key).like(func.concat(Namespace.namespace, ".%")))
        .order_by(func.length(Namespace.namespace).desc())
    )
    candidates = result.scalars().all()
    for ns in candidates:
        if key_matches(ns.namespace, ns.key_template, fact_key):
            return ns
    nearest = candidates[0] if candidates else None
    raise KeyRuleError(
        "fact_key matches no namespace template; call list_namespaces and build_fact_key",
        fact_key=fact_key,
        nearest=(
            {"namespace": nearest.namespace, "key_template": nearest.key_template}
            if nearest
            else None
        ),
    )


async def list_fact_index(
    session: AsyncSession,
    project_path: str,
    *,
    namespace: str,
    status: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    """The L2 index: key, summary and revision of each fact in one namespace."""
    project = await require_project(session, project_path)
    ns = await get_namespace(session, project, namespace)
    capped, skip = list_limit(limit), clean_offset(offset)
    stmt = select(Fact).where(Fact.namespace_id == ns.id)
    if status:
        stmt = stmt.where(Fact.status == status)
    stmt = stmt.order_by(Fact.updated_at.desc()).offset(skip).limit(capped)
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "namespace": ns.namespace,
        "key_template": ns.key_template,
        "items": [
            {
                "fact_key": f.fact_key,
                "kind": f.kind.value,
                "status": f.status,
                "summary": f.summary,
                "revision": f.revision,
                "updated_at": iso(f.updated_at),
            }
            for f in rows
        ],
        "limit": capped,
        "offset": skip,
    }
