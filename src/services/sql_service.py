"""Read-only SQL over the caller's own L4 raw events.

The query may read only l4_raw_events and call only allow-listed functions. Every
reference to the table is replaced by a subquery already filtered to the caller's
project. The statement runs in a READ ONLY transaction with a timeout, as the role
SQL_READER_ROLE (SELECT on l4_raw_events, nothing else), and LIMIT is forced to at most
SQL_FORCE_LIMIT rows.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.services.l1_service import require_project
from src.services.partition_service import PARTITION_NAME_RE
from src.services.sanitize import sanitize_and_truncate

ALLOWED_TABLE = "l4_raw_events"

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|execute|call|"
    r"merge|attach|detach|lock|vacuum|set|reset|listen|notify|into|"
    r"pg_\w+|dblink\w*|lo_\w+|information_schema|current_setting|set_config)\b",
    re.IGNORECASE,
)
# Functions a query may call. Anything else could run SQL from a string (query_to_xml),
# change the session (set_config) or reach the server (pg_read_file), none of which the
# query plan shows.
_ALLOWED_FUNCTIONS = frozenset({
    "count", "sum", "avg", "min", "max", "string_agg", "array_agg", "bool_and", "bool_or",
    "lower", "upper", "length", "char_length", "substring", "substr", "left", "right",
    "position", "strpos", "trim", "btrim", "ltrim", "rtrim", "replace", "split_part",
    "concat", "concat_ws", "regexp_replace", "regexp_match", "regexp_matches",
    "date_trunc", "date_part", "extract", "now", "age", "to_char", "to_timestamp", "to_date",
    "make_interval", "abs", "round", "floor", "ceil", "coalesce", "nullif", "greatest",
    "least", "cast", "row_number", "rank", "dense_rank", "lag", "lead", "first_value",
    "last_value", "similarity", "word_similarity",
})
# Keywords and type names that may stand right before "(".
_PAREN_KEYWORDS = frozenset({
    "select", "from", "join", "where", "and", "or", "not", "in", "exists", "any", "all",
    "some", "on", "over", "filter", "within", "as", "when", "then", "else", "by",
    "distinct", "array", "row", "using", "like", "ilike", "having", "between", "is",
    "lateral", "union", "except", "intersect", "varchar", "char", "numeric", "decimal",
})
_CALL = re.compile(r"([\w$.\"]+)\s*\(")
_TABLE_REF = re.compile(r"\b(from|join)\s+([\"\w.]+)", re.IGNORECASE)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def ensure_limit(sql_query: str, limit: int | None = None) -> str:
    cap = limit if limit is not None else get_settings().sql_force_limit
    stripped = sql_query.strip().rstrip(";")
    if re.search(r"\blimit\s+\d+\b", stripped, re.IGNORECASE):
        return re.sub(
            r"\blimit\s+(\d+)\b",
            lambda m: f"LIMIT {min(int(m.group(1)), cap)}",
            stripped,
            flags=re.IGNORECASE,
        )
    return f"{stripped} LIMIT {cap}"


def validate_query(sql_query: str) -> str:
    stripped = (sql_query or "").strip()
    if not stripped:
        raise ValueError("Empty SQL query")
    cleaned = stripped.rstrip(";")
    if ";" in cleaned or "--" in cleaned or "/*" in cleaned:
        raise ValueError("One statement only, without comments")
    if not re.match(r"^\s*select\b", cleaned, re.IGNORECASE):
        raise ValueError("Only SELECT statements are allowed")
    # Keywords inside string literals ('%do not%') are data, not SQL.
    code = _STRING_LITERAL.sub("''", cleaned)
    if _FORBIDDEN.search(code):
        raise ValueError("Statement uses a keyword or function that is not allowed")
    for match in _CALL.finditer(code):
        name = match.group(1).lower()
        if '"' in name or "." in name:
            raise ValueError("Quoted or schema-qualified function calls are not allowed")
        if name not in _ALLOWED_FUNCTIONS and name not in _PAREN_KEYWORDS:
            raise ValueError(f"Function {name} is not allowed")
    tables = {m.group(2).strip('"').lower() for m in _TABLE_REF.finditer(code)}
    if not tables:
        raise ValueError(f"Query must read FROM {ALLOWED_TABLE}")
    if tables - {ALLOWED_TABLE}:
        raise ValueError(f"Only {ALLOWED_TABLE} can be queried")
    return cleaned


def scope_to_project(sql_query: str) -> str:
    scoped = (
        f"(SELECT * FROM {ALLOWED_TABLE} WHERE project_path = :project_path) AS {ALLOWED_TABLE}"
    )
    return _TABLE_REF.sub(lambda m: f"{m.group(1)} {scoped}", sql_query)


def plan_relations(plan: Any) -> set[str]:
    """Every table name in an EXPLAIN (FORMAT JSON) plan."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "Relation Name" in node:
                found.add(str(node["Relation Name"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(json.loads(plan) if isinstance(plan, str) else plan)
    return found


def _allowed_relation(name: str) -> bool:
    return name == ALLOWED_TABLE or bool(PARTITION_NAME_RE.match(name))


async def query_deep_memory_sql(session: AsyncSession, project_path: str, sql_query: str) -> dict:
    project = await require_project(session, project_path)
    final_sql = ensure_limit(scope_to_project(validate_query(sql_query)))
    params = {"project_path": project.project_path}
    await session.execute(text("SET TRANSACTION READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    # A validated identifier (Settings.validate_role_name).
    await session.execute(text(f"SET LOCAL ROLE {get_settings().sql_reader_role}"))
    try:
        # The regex checks are a first filter; the planner is the authority on what is read.
        plan = (
            await session.execute(text(f"EXPLAIN (FORMAT JSON) {final_sql}"), params)
        ).scalar_one()
        extra = sorted(r for r in plan_relations(plan) if not _allowed_relation(r))
        if extra:
            raise ValueError(f"Only {ALLOWED_TABLE} can be queried (query reads {extra})")
        result = await session.execute(text(final_sql), params)
    except DBAPIError as exc:
        # Permission, syntax and timeout errors go back to the agent as an invalid query.
        detail = str(exc.orig).splitlines()[0] if exc.orig else "query failed"
        raise ValueError(f"Query failed: {detail}") from exc
    rows = []
    for mapping in result.mappings().all():
        rows.append(
            {
                key: sanitize_and_truncate(value)
                if isinstance(value, str)
                else (value if value is None or isinstance(value, (int, float, bool)) else str(value))
                for key, value in mapping.items()
            }
        )
    return {"executed_sql": final_sql, "count": len(rows), "rows": rows}
