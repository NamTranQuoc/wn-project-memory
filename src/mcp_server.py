"""MCP stdio server exposing hierarchical memory tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer

from src.core.db import AsyncSessionLocal
from src.services import (
    fact_service,
    l1_reference_service,
    memory_service,
    search_service,
    source_service,
    sql_service,
    task_service,
    watched_ref_service,
    watermark_service,
)
from src.services.sanitize import sanitize_and_truncate

logging.basicConfig(level=logging.INFO)

mcp = MCPServer("agentic-memory")


def _dump(payload: Any) -> str:
    text = json.dumps(payload, default=str, ensure_ascii=False)
    return sanitize_and_truncate(text)


@mcp.tool()
async def init_project_memory(
    project_path: str,
    initial_context: str,
    sources_json: str = "[]",
) -> str:
    """Initialize L0/L2, project registry, user_session source, and optional sources.

    sources_json: JSON array of
      {source_key, source_type, display_name?, connection_config?, read_recipe?}
    """
    try:
        sources = json.loads(sources_json) if sources_json else []
        if not isinstance(sources, list):
            return _dump({"error": "sources_json must be a JSON array"})
    except json.JSONDecodeError as exc:
        return _dump({"error": f"invalid sources_json: {exc}"})

    async with AsyncSessionLocal() as session:
        result = await memory_service.init_project_memory(
            session, project_path, initial_context, sources=sources
        )
        await session.commit()
        return _dump(result)


@mcp.tool()
async def upsert_distilled_rule(
    project_path: str,
    entity_key: str,
    content: str,
    raw_event_id: str,
    source_hash: str,
) -> str:
    """Upsert an L3 distilled rule using dual-hash overwrite semantics."""
    async with AsyncSessionLocal() as session:
        result = await memory_service.upsert_distilled_rule(
            session,
            project_path=project_path,
            entity_key=entity_key,
            content=content,
            raw_event_id=raw_event_id or None,
            source_hash=source_hash,
        )
        await session.commit()
        return _dump(result)


@mcp.tool()
async def search_memory(
    project_path: str,
    query: str,
    search_type: str = "hybrid",
    limit: int = 5,
) -> str:
    """Search L3 distilled knowledge (semantic, keyword, or hybrid). Default limit=5."""
    async with AsyncSessionLocal() as session:
        result = await search_service.search_memory(
            session,
            project_path=project_path,
            query=query,
            search_type=search_type,
            limit=limit,
        )
        await session.commit()
        return _dump(result)


@mcp.tool()
async def query_deep_memory_sql(project_path: str, sql_query: str) -> str:
    """Read L4 raw events with a SELECT query. Backend force-injects LIMIT 10."""
    async with AsyncSessionLocal() as session:
        result = await sql_service.query_deep_memory_sql(
            session, project_path=project_path, sql_query=sql_query
        )
        await session.commit()
        return _dump(result)


@mcp.tool()
async def log_raw_event(
    project_path: str,
    event_type: str,
    content: str,
    source_hash: str = "",
    source_key: str = "",
) -> str:
    """Append a raw event to L4 for provenance/audit. Does not extract facts —
    read the raw content yourself and call upsert_fact/upsert_task/
    upsert_watched_ref/upsert_distilled_rule directly with raw_event_id set.

    source_key: optional registered source; falls back to user_session.
    """
    async with AsyncSessionLocal() as session:
        result = await memory_service.log_raw_event(
            session,
            project_path=project_path,
            event_type=event_type,
            content=content,
            source_hash=source_hash or None,
            source_key=source_key or None,
        )
        await session.commit()
        return _dump(result)


@mcp.tool()
async def get_raw_context(project_path: str, raw_event_id: str) -> str:
    """Return the full raw L4 event content (escape hatch for truncated search results)."""
    async with AsyncSessionLocal() as session:
        result = await memory_service.get_raw_context(
            session, project_path=project_path, raw_event_id=raw_event_id
        )
        await session.commit()
        return json.dumps(result, default=str, ensure_ascii=False)


@mcp.tool()
async def update_working_memory(project_path: str, current_focus_text: str) -> str:
    """Update L0 working-memory scratchpad for the current project focus."""
    async with AsyncSessionLocal() as session:
        result = await memory_service.update_working_memory(
            session,
            project_path=project_path,
            current_focus_text=current_focus_text,
        )
        await session.commit()
        return _dump(result)


@mcp.tool()
async def register_data_source(
    project_path: str,
    source_key: str,
    source_type: str,
    display_name: str = "",
    connection_config_json: str = "{}",
    read_recipe: str = "",
) -> str:
    """Register or update a data source for a project (after init or at any time)."""
    try:
        connection_config = json.loads(connection_config_json) if connection_config_json else {}
    except json.JSONDecodeError as exc:
        return _dump({"error": f"invalid connection_config_json: {exc}"})

    async with AsyncSessionLocal() as session:
        try:
            result = await source_service.register_data_source(
                session,
                project_path,
                source_key=source_key,
                source_type=source_type,
                display_name=display_name or None,
                connection_config=connection_config or None,
                read_recipe=read_recipe or None,
                added_via="manual",
            )
            await session.commit()
            return _dump(result)
        except ValueError as exc:
            return _dump({"error": str(exc)})


@mcp.tool()
async def list_data_sources(project_path: str, active_only: bool = True) -> str:
    """List registered data sources for a project."""
    async with AsyncSessionLocal() as session:
        result = await source_service.list_data_sources(
            session, project_path, active_only=active_only
        )
        await session.commit()
        return _dump(result)


@mcp.tool()
async def upsert_watermark(
    project_path: str,
    source_key: str,
    stream_key: str = "",
    indexed_through_json: str = "{}",
    full_read_ids_json: str = "[]",
    known_gaps_json: str = "[]",
    checked_at: str = "",
) -> str:
    """Upsert a cursor/watermark for a registered source stream."""
    try:
        indexed_through = json.loads(indexed_through_json or "{}")
        full_read_ids = json.loads(full_read_ids_json or "[]")
        known_gaps = json.loads(known_gaps_json or "[]")
    except json.JSONDecodeError as exc:
        return _dump({"error": f"invalid JSON payload: {exc}"})

    async with AsyncSessionLocal() as session:
        try:
            result = await watermark_service.upsert_watermark(
                session,
                project_path,
                source_key=source_key,
                stream_key=stream_key or None,
                indexed_through=indexed_through,
                full_read_ids=full_read_ids,
                known_gaps=known_gaps,
                checked_at=checked_at or None,
            )
            await session.commit()
            return _dump(result)
        except ValueError as exc:
            return _dump({"error": str(exc)})


@mcp.tool()
async def get_watermark(project_path: str, source_key: str, stream_key: str = "") -> str:
    """Get a watermark for a source stream."""
    async with AsyncSessionLocal() as session:
        result = await watermark_service.get_watermark(
            session, project_path, source_key=source_key, stream_key=stream_key or None
        )
        await session.commit()
        return _dump(result)


@mcp.tool()
async def list_watermarks(project_path: str) -> str:
    """List all watermarks for a project."""
    async with AsyncSessionLocal() as session:
        result = await watermark_service.list_watermarks(session, project_path)
        await session.commit()
        return _dump(result)


@mcp.tool()
async def upsert_fact(
    project_path: str,
    kind: str,
    title: str,
    content: str,
    priority: int = 0,
    status: str = "",
    occurred_at: str = "",
    source_key: str = "",
) -> str:
    """Upsert an operational fact (kind: fact/decision/plan/question/issue/solution)."""
    async with AsyncSessionLocal() as session:
        try:
            result = await fact_service.upsert_fact(
                session,
                project_path,
                kind=kind,
                title=title,
                content=content,
                priority=priority,
                status=status or None,
                occurred_at=occurred_at or None,
                source_key=source_key or None,
            )
            await session.commit()
            return _dump(result)
        except ValueError as exc:
            return _dump({"error": str(exc)})


@mcp.tool()
async def search_facts(
    project_path: str,
    query: str,
    search_type: str = "hybrid",
    kind: str = "",
    order_by: str = "relevance",
    limit: int = 5,
) -> str:
    """Semantic/keyword/hybrid search over l3_facts."""
    async with AsyncSessionLocal() as session:
        try:
            result = await fact_service.search_facts(
                session,
                project_path,
                query,
                search_type=search_type,
                kind=kind or None,
                order_by=order_by,
                limit=limit,
            )
            await session.commit()
            return _dump(result)
        except ValueError as exc:
            return _dump({"error": str(exc)})


@mcp.tool()
async def upsert_task(
    project_path: str,
    task_key: str,
    title: str,
    content: str,
    status: str = "open",
    priority: int = 0,
    waiting_on: str = "",
    source_key: str = "",
) -> str:
    """Upsert an operational task / open-loop (stable task_key)."""
    async with AsyncSessionLocal() as session:
        try:
            result = await task_service.upsert_task(
                session,
                project_path,
                task_key=task_key,
                title=title,
                content=content,
                status=status,
                priority=priority,
                waiting_on=waiting_on or None,
                source_key=source_key or None,
            )
            await session.commit()
            return _dump(result)
        except ValueError as exc:
            return _dump({"error": str(exc)})


@mcp.tool()
async def close_task(project_path: str, task_key: str, content: str = "") -> str:
    """Soft-close a task by task_key."""
    async with AsyncSessionLocal() as session:
        result = await task_service.close_task(
            session, project_path, task_key, content=content or None
        )
        await session.commit()
        return _dump(result)


@mcp.tool()
async def list_tasks(
    project_path: str,
    status: str = "",
    order_by: str = "priority",
    limit: int = 5,
) -> str:
    """List tasks filtered by status, ordered by priority or since_at."""
    async with AsyncSessionLocal() as session:
        try:
            result = await task_service.list_tasks(
                session,
                project_path,
                status=status or None,
                order_by=order_by,
                limit=limit,
            )
            await session.commit()
            return _dump(result)
        except ValueError as exc:
            return _dump({"error": str(exc)})


@mcp.tool()
async def upsert_watched_ref(
    project_path: str,
    ref_type: str,
    ref_value: str,
    why: str = "",
    status_note: str = "",
    disposition: str = "queued",
    source_key: str = "",
) -> str:
    """Upsert a watched reference (PR/issue/SHA/path/ticket/tag)."""
    async with AsyncSessionLocal() as session:
        try:
            result = await watched_ref_service.upsert_watched_ref(
                session,
                project_path,
                ref_type=ref_type,
                ref_value=ref_value,
                why=why or None,
                status_note=status_note or None,
                disposition=disposition,
                source_key=source_key or None,
            )
            await session.commit()
            return _dump(result)
        except ValueError as exc:
            return _dump({"error": str(exc)})


@mcp.tool()
async def list_watched_refs(
    project_path: str,
    disposition: str = "",
    limit: int = 5,
) -> str:
    """List watched refs, optionally filtered by disposition."""
    async with AsyncSessionLocal() as session:
        try:
            result = await watched_ref_service.list_watched_refs(
                session,
                project_path,
                disposition=disposition or None,
                limit=limit,
            )
            await session.commit()
            return _dump(result)
        except ValueError as exc:
            return _dump({"error": str(exc)})


@mcp.tool()
async def upsert_l1_reference(
    project_path: str,
    ref_key: str,
    title: str,
    content: str,
    is_policy: bool = False,
    priority: int = 0,
    source_key: str = "",
    raw_event_id: str = "",
) -> str:
    """Create or overwrite-in-place a curated L1 reference doc (roster, DoD, policy, etc.)."""
    async with AsyncSessionLocal() as session:
        try:
            result = await l1_reference_service.upsert_l1_reference(
                session,
                project_path,
                ref_key=ref_key,
                title=title,
                content=content,
                is_policy=is_policy,
                priority=priority,
                source_key=source_key or None,
                raw_event_id=raw_event_id or None,
            )
            await session.commit()
            return _dump(result)
        except ValueError as exc:
            return _dump({"error": str(exc)})


@mcp.tool()
async def get_l1_reference(project_path: str, ref_key: str) -> str:
    """Return the full, untruncated content of one L1 reference doc by ref_key."""
    async with AsyncSessionLocal() as session:
        result = await l1_reference_service.get_l1_reference(session, project_path, ref_key)
        await session.commit()
        return json.dumps(result, default=str, ensure_ascii=False)


@mcp.tool()
async def list_l1_references(project_path: str, policy_only: bool = False) -> str:
    """List L1 reference docs (ref_key, title, is_policy, priority) — content omitted."""
    async with AsyncSessionLocal() as session:
        result = await l1_reference_service.list_l1_references(
            session, project_path, policy_only=policy_only
        )
        await session.commit()
        return _dump(result)


@mcp.tool()
async def search_l1_references(
    project_path: str,
    query: str,
    search_type: str = "hybrid",
    limit: int = 5,
) -> str:
    """Semantic/keyword/hybrid search over l1_references.content (truncated results)."""
    async with AsyncSessionLocal() as session:
        try:
            result = await l1_reference_service.search_l1_references(
                session, project_path, query, search_type=search_type, limit=limit
            )
            await session.commit()
            return _dump(result)
        except ValueError as exc:
            return _dump({"error": str(exc)})


@mcp.tool()
async def get_active_policies(project_path: str) -> str:
    """Return full, untruncated content of every is_policy=true L1 reference, priority-ordered."""
    async with AsyncSessionLocal() as session:
        result = await l1_reference_service.get_active_policies(session, project_path)
        await session.commit()
        return json.dumps(result, default=str, ensure_ascii=False)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
