"""MCP stdio server exposing hierarchical memory tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer

from src.core.db import AsyncSessionLocal
from src.services import distillation_service, memory_service, search_service, sql_service
from src.services.sanitize import sanitize_and_truncate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = MCPServer("agentic-memory")


def _dump(payload: Any) -> str:
    text = json.dumps(payload, default=str, ensure_ascii=False)
    return sanitize_and_truncate(text)


@mcp.tool()
async def init_project_memory(project_path: str, initial_context: str) -> str:
    """Initialize L1 working memory and L2 meta memory for a project path."""
    async with AsyncSessionLocal() as session:
        result = await memory_service.init_project_memory(
            session, project_path, initial_context
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
) -> str:
    """Append a raw event to L4 (pending) and trigger background distillation."""
    async with AsyncSessionLocal() as session:
        result = await memory_service.log_raw_event(
            session,
            project_path=project_path,
            event_type=event_type,
            content=content,
            source_hash=source_hash or None,
        )
        await session.commit()
        # Best-effort immediate distillation; scheduler retries on failure
        try:
            await distillation_service.distill_event_by_id(session, result["id"])
            await session.commit()
        except Exception:
            logger.exception("Immediate distillation failed; scheduler will retry")
            await session.rollback()
        return _dump(result)


@mcp.tool()
async def get_raw_context(project_path: str, raw_event_id: str) -> str:
    """Return the full raw L4 event content (escape hatch for truncated search results)."""
    async with AsyncSessionLocal() as session:
        result = await memory_service.get_raw_context(
            session, project_path=project_path, raw_event_id=raw_event_id
        )
        await session.commit()
        # Full content intentionally not truncated for this tool
        return json.dumps(result, default=str, ensure_ascii=False)


@mcp.tool()
async def update_working_memory(project_path: str, current_focus_text: str) -> str:
    """Update L1 working-memory scratchpad for the current project focus."""
    async with AsyncSessionLocal() as session:
        result = await memory_service.update_working_memory(
            session,
            project_path=project_path,
            current_focus_text=current_focus_text,
        )
        await session.commit()
        return _dump(result)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
