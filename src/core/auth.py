"""Optional API-key gate for the REST surface.

MCP stdio stays out of band (local process trust). When MEMORY_API_KEY is set,
all HTTP routes except health/docs require X-API-Key or Authorization: Bearer.
"""

from __future__ import annotations

import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.core.config import get_settings

logger = logging.getLogger(__name__)

# Unauthenticated probes / OpenAPI only — never memory data routes.
_PUBLIC_PATH_PREFIXES = (
    "/health",
    "/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _is_public_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _PUBLIC_PATH_PREFIXES)


def _extract_api_key(request: Request) -> str:
    header_key = request.headers.get("X-API-Key", "").strip()
    if header_key:
        return header_key
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _keys_match(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    # compare_digest requires equal length; unequal → reject without raising.
    if len(provided) != len(expected):
        return False
    return secrets.compare_digest(provided, expected)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        expected = settings.memory_api_key
        if not expected:
            return await call_next(request)
        if _is_public_path(request.url.path):
            return await call_next(request)
        if not _keys_match(_extract_api_key(request), expected):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


def warn_if_rest_unauthenticated() -> None:
    """Log a clear warning when the REST app is network-bindable without a key."""
    settings = get_settings()
    if settings.memory_api_key:
        return
    logger.warning(
        "MEMORY_API_KEY is unset — REST endpoints are open. "
        "Set MEMORY_API_KEY before exposing APP_HOST=%s beyond localhost "
        "(MCP stdio is unaffected).",
        settings.app_host,
    )
