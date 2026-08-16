"""Guarded external reindex reset and legacy inventory helpers."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import (
    DataSource,
    L3DistilledKnowledge,
    L3Fact,
    L3SourceUnit,
    L3Task,
    L3WatchedRef,
    L3Watermark,
)
from src.services.project_service import get_or_create_project
from src.services.sanitize import sanitize_and_truncate
from src.services.source_service import (
    LEGACY_UNATTRIBUTED_KEY,
    is_protected_reindex_source,
    seed_legacy_unattributed_source,
)


def _clamp_limit(limit: int | None) -> int:
    settings = get_settings()
    if limit is None:
        return settings.default_search_limit
    return max(1, min(int(limit), settings.max_search_limit))


async def _resolve_external_sources(
    session: AsyncSession,
    project_path: str,
    source_keys: list[str] | None,
) -> list[DataSource]:
    project = await get_or_create_project(session, project_path)
    await seed_legacy_unattributed_source(
        session, project_id=project.id, project_path=project_path
    )

    stmt = select(DataSource).where(
        DataSource.project_id == project.id,
        DataSource.is_active.is_(True),
    )
    if source_keys:
        cleaned = [k.strip() for k in source_keys if k and str(k).strip()]
        if not cleaned:
            raise ValueError("source_keys must be a non-empty list when provided")
        stmt = stmt.where(DataSource.source_key.in_(cleaned))
    result = await session.execute(stmt.order_by(DataSource.source_key.asc()))
    rows = list(result.scalars().all())

    if source_keys:
        found = {r.source_key for r in rows}
        missing = sorted(set(cleaned) - found)
        if missing:
            raise ValueError(f"Unknown or inactive source_keys: {missing}")

    protected = [r.source_key for r in rows if is_protected_reindex_source(r)]
    if protected:
        raise ValueError(
            "Protected sources cannot be reindexed/reset: "
            f"{sorted(set(protected))}. Exclude user_session, local_file, "
            f"and {LEGACY_UNATTRIBUTED_KEY}."
        )

    external = [r for r in rows if not is_protected_reindex_source(r)]
    if not external:
        raise ValueError("No eligible external sources to reindex")
    return external


async def _count_for_sources(
    session: AsyncSession,
    model: type,
    source_ids: list[UUID],
) -> int:
    if not source_ids:
        return 0
    result = await session.execute(
        select(func.count()).select_from(model).where(model.source_id.in_(source_ids))
    )
    return int(result.scalar_one())


async def _sample_keys(
    session: AsyncSession,
    *,
    model: type,
    source_ids: list[UUID],
    key_attr: str,
    limit: int = 5,
) -> list[str]:
    if not source_ids:
        return []
    col = getattr(model, key_attr)
    result = await session.execute(
        select(col).where(model.source_id.in_(source_ids)).order_by(col.asc()).limit(limit)
    )
    return [str(v) for (v,) in result.all() if v is not None]


async def preview_external_reindex(
    session: AsyncSession,
    project_path: str,
    *,
    source_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Dry-run: counts and sample keys that would be cleared on apply."""
    sources = await _resolve_external_sources(session, project_path, source_keys)
    source_ids = [s.id for s in sources]
    by_source: list[dict[str, Any]] = []
    for src in sources:
        sid = [src.id]
        by_source.append(
            {
                "source_key": src.source_key,
                "source_type": src.source_type.value,
                "source_id": str(src.id),
                "counts": {
                    "source_units": await _count_for_sources(session, L3SourceUnit, sid),
                    "watermarks": await _count_for_sources(session, L3Watermark, sid),
                    "facts": await _count_for_sources(session, L3Fact, sid),
                    "distilled_rules": await _count_for_sources(
                        session, L3DistilledKnowledge, sid
                    ),
                },
                "sample_fact_keys": await _sample_keys(
                    session, model=L3Fact, source_ids=sid, key_attr="fact_key"
                ),
                "sample_entity_keys": await _sample_keys(
                    session,
                    model=L3DistilledKnowledge,
                    source_ids=sid,
                    key_attr="entity_key",
                ),
            }
        )

    preserved = {
        "sources_registry": True,
        "l1_references": True,
        "l2_meta_memory": True,
        "l4_raw_events": True,
        "tasks": await session.scalar(
            select(func.count()).select_from(L3Task).where(L3Task.project_path == project_path)
        ),
        "watched_refs": await session.scalar(
            select(func.count())
            .select_from(L3WatchedRef)
            .where(L3WatchedRef.project_path == project_path)
        ),
    }

    totals = {
        "source_units": sum(s["counts"]["source_units"] for s in by_source),
        "watermarks": sum(s["counts"]["watermarks"] for s in by_source),
        "facts": sum(s["counts"]["facts"] for s in by_source),
        "distilled_rules": sum(s["counts"]["distilled_rules"] for s in by_source),
    }
    return {
        "project_path": project_path,
        "mode": "preview",
        "sources": by_source,
        "totals": totals,
        "preserved": {
            "sources_registry": preserved["sources_registry"],
            "l1_references": preserved["l1_references"],
            "l2_meta_memory": preserved["l2_meta_memory"],
            "l4_raw_events": preserved["l4_raw_events"],
            "tasks": int(preserved["tasks"] or 0),
            "watched_refs": int(preserved["watched_refs"] or 0),
        },
        "note": (
            "Apply with confirm=true to delete only external-derived "
            "source_units, watermarks, facts, and distilled rules for these sources. "
            "Never call init_project_memory during reindex."
        ),
    }


async def apply_external_reindex_reset(
    session: AsyncSession,
    project_path: str,
    *,
    source_keys: list[str] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Clear external-derived current-state rows so a cold crawl can rebuild them."""
    if not confirm:
        raise ValueError("confirm=true is required to apply external reindex reset")

    preview = await preview_external_reindex(
        session, project_path, source_keys=source_keys
    )
    sources = await _resolve_external_sources(session, project_path, source_keys)
    source_ids = [s.id for s in sources]

    deleted = {
        "source_units": 0,
        "watermarks": 0,
        "facts": 0,
        "distilled_rules": 0,
    }
    if source_ids:
        # Clear optional FKs into distilled rules before deleting those rules.
        rule_ids = select(L3DistilledKnowledge.id).where(
            L3DistilledKnowledge.source_id.in_(source_ids)
        )
        for model in (L3Fact, L3Task, L3WatchedRef, L3Watermark):
            await session.execute(
                model.__table__.update()
                .where(model.l3_entity_id.in_(rule_ids))
                .values(l3_entity_id=None)
            )

        for model, key in (
            (L3SourceUnit, "source_units"),
            (L3Watermark, "watermarks"),
            (L3Fact, "facts"),
            (L3DistilledKnowledge, "distilled_rules"),
        ):
            result = await session.execute(
                delete(model).where(model.source_id.in_(source_ids))
            )
            deleted[key] = int(result.rowcount or 0)

    await session.flush()
    return {
        "project_path": project_path,
        "mode": "applied",
        "sources": [s["source_key"] for s in preview["sources"]],
        "deleted": deleted,
        "preserved": preview["preserved"],
        "next_step": (
            "Cold-read each external source via its read_recipe, ingest_source_unit, "
            "then upsert facts/rules with stable keys. Do not advance watermarks "
            "until the stream succeeds."
        ),
    }


async def inventory_legacy_state(
    session: AsyncSession,
    project_path: str,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """List legacy:* keys and rows attributed to legacy_unattributed."""
    capped = _clamp_limit(limit)
    project = await get_or_create_project(session, project_path)
    legacy_src = await seed_legacy_unattributed_source(
        session, project_id=project.id, project_path=project_path
    )

    fact_result = await session.execute(
        select(L3Fact)
        .where(
            L3Fact.project_path == project_path,
            (L3Fact.fact_key.like("legacy:%")) | (L3Fact.source_id == legacy_src.id),
        )
        .order_by(L3Fact.updated_at.desc())
        .limit(capped)
    )
    rule_result = await session.execute(
        select(L3DistilledKnowledge)
        .where(
            L3DistilledKnowledge.project_path == project_path,
            (L3DistilledKnowledge.entity_key.like("legacy:%"))
            | (L3DistilledKnowledge.source_id == legacy_src.id),
        )
        .order_by(L3DistilledKnowledge.last_verified_at.desc())
        .limit(capped)
    )

    facts = [
        {
            "id": str(r.id),
            "fact_key": r.fact_key,
            "kind": r.kind.value,
            "title": sanitize_and_truncate(r.title),
            "content": sanitize_and_truncate(r.content),
            "source_id": str(r.source_id),
            "raw_event_id": str(r.raw_event_id) if r.raw_event_id else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in fact_result.scalars().all()
    ]
    rules = [
        {
            "id": str(r.id),
            "entity_key": r.entity_key,
            "content": sanitize_and_truncate(r.content),
            "source_id": str(r.source_id),
            "raw_event_id": str(r.raw_event_id) if r.raw_event_id else None,
            "last_verified_at": (
                r.last_verified_at.isoformat() if r.last_verified_at else None
            ),
        }
        for r in rule_result.scalars().all()
    ]
    return {
        "project_path": project_path,
        "legacy_source_key": LEGACY_UNATTRIBUTED_KEY,
        "legacy_source_id": str(legacy_src.id),
        "limit": capped,
        "facts": facts,
        "rules": rules,
        "count_facts": len(facts),
        "count_rules": len(rules),
        "note": (
            "Propose a reconciliation map (canonical key + content + source) "
            "and get explicit approval before upserting canonical rows and "
            "deleting legacy keys."
        ),
    }
