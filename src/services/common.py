"""Small helpers shared by every service: limits, ids, timestamps, domain errors."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src.core.config import get_settings


class DomainError(Exception):
    """A refusal the agent can act on: MCP tools return payload(), REST answers status."""

    status = 422

    def payload(self) -> dict:
        raise NotImplementedError


class NotFoundError(DomainError, LookupError):
    """A named row does not exist."""

    status = 404

    def __init__(self, what: str, **detail: object) -> None:
        super().__init__(what)
        self.what = what
        self.detail = detail

    def payload(self) -> dict:
        return {"error": "not_found", "what": self.what, **self.detail}


def error_payload(exc: Exception) -> dict:
    """What a tool returns for a domain error or an invalid argument (ValueError)."""
    if isinstance(exc, DomainError):
        return exc.payload()
    return {"error": "invalid", "message": str(exc)}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def parse_uuid(value: uuid.UUID | str | None, *, field: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def search_limit(limit: int | None) -> int:
    """Search results: default 5, hard cap 5."""
    settings = get_settings()
    if limit is None:
        return settings.default_limit
    return max(1, min(int(limit), settings.max_search_limit))


def list_limit(limit: int | None) -> int:
    """Pageable lists: default 5, cap max_list_limit."""
    settings = get_settings()
    if limit is None:
        return settings.default_limit
    return max(1, min(int(limit), settings.max_list_limit))


def clean_offset(offset: int | None) -> int:
    return max(0, int(offset or 0))


LIKE_ESCAPE = "\\"


def contains_pattern(query: str) -> str:
    """ILIKE pattern that matches the query literally; use with escape=LIKE_ESCAPE."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def check_length(value: str, *, field: str, limit: int) -> str:
    """Refuse oversized writes so one payload cannot bloat the database or a context."""
    if len(value) > limit:
        raise ValueError(
            f"{field} has {len(value)} characters; the limit is {limit}. "
            "Store a shorter summary and cite the source_url for the rest."
        )
    return value


def require_text(value: str | None, *, field: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{field} is required")
    return str(value).strip()
