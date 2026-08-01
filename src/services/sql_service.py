from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.services.sanitize import sanitize_and_truncate

FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|"
    r"copy|execute|call|merge|replace|attach|detach)\b",
    re.IGNORECASE,
)


def ensure_limit(sql_query: str, limit: int | None = None) -> str:
    """Force-inject LIMIT N when the caller omitted it."""
    cap = limit if limit is not None else get_settings().sql_force_limit
    stripped = sql_query.strip().rstrip(";")
    if re.search(r"\blimit\s+\d+\b", stripped, re.IGNORECASE):
        # Cap any existing LIMIT to max allowed
        def _cap_limit(match: re.Match[str]) -> str:
            value = int(match.group(1))
            return f"LIMIT {min(value, cap)}"

        return re.sub(r"\blimit\s+(\d+)\b", _cap_limit, stripped, flags=re.IGNORECASE)
    return f"{stripped} LIMIT {cap}"


def validate_select_only(sql_query: str) -> str:
    stripped = sql_query.strip()
    if not stripped:
        raise ValueError("Empty SQL query")
    if ";" in stripped.rstrip(";"):
        raise ValueError("Multiple statements are not allowed")
    cleaned = stripped.rstrip(";")
    if not re.match(r"^\s*select\b", cleaned, re.IGNORECASE):
        raise ValueError("Only SELECT statements are allowed")
    if FORBIDDEN_KEYWORDS.search(cleaned):
        raise ValueError("DDL/DML keywords are not allowed")
    return cleaned


async def query_deep_memory_sql(
    session: AsyncSession,
    project_path: str,
    sql_query: str,
) -> dict:
    """
    Execute a read-only SELECT against L4, scoped to project_path.
    Automatically wraps LIMIT 10 per CLAUDE.md rules.
    """
    validated = validate_select_only(sql_query)
    limited = ensure_limit(validated)

    # Scope filter: wrap as subquery and enforce project_path
    scoped = (
        f"SELECT * FROM ({limited}) AS deep_q "
        f"WHERE project_path = :project_path "
        f"LIMIT {get_settings().sql_force_limit}"
    )

    try:
        result = await session.execute(
            text(scoped),
            {"project_path": project_path},
        )
    except Exception:
        # If subquery projection lacks project_path column, fall back to raw limited query
        # but still bind project_path into a wrapper that filters L4 table alias if present.
        # Prefer injecting WHERE project_path = :project_path when possible.
        if "project_path" not in limited.lower():
            if re.search(r"\bwhere\b", limited, re.IGNORECASE):
                injected = re.sub(
                    r"\bwhere\b",
                    "WHERE project_path = :project_path AND",
                    limited,
                    count=1,
                    flags=re.IGNORECASE,
                )
            else:
                # Insert before ORDER BY / LIMIT if present
                injected = re.sub(
                    r"\b(order\s+by|limit)\b",
                    r"WHERE project_path = :project_path \1",
                    limited,
                    count=1,
                    flags=re.IGNORECASE,
                )
                if injected == limited:
                    injected = f"{limited} WHERE project_path = :project_path"
            limited = ensure_limit(injected)
        result = await session.execute(
            text(limited),
            {"project_path": project_path},
        )

    rows = []
    for mapping in result.mappings().all():
        sanitized_row = {}
        for key, value in dict(mapping).items():
            if isinstance(value, str):
                sanitized_row[key] = sanitize_and_truncate(value)
            else:
                sanitized_row[key] = (
                    str(value) if value is not None and not isinstance(value, (int, float, bool)) else value
                )
        rows.append(sanitized_row)

    return {
        "project_path": project_path,
        "executed_sql": limited,
        "count": len(rows),
        "rows": rows,
    }
