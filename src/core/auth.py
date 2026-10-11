"""API-key gate for every HTTP route (REST and /mcp).

MEMORY_API_KEY is required at startup (see Settings.require_api_key), so this
middleware never runs open. Only health probes are public. Pure ASGI so that
streamed MCP responses pass through untouched.
"""

from __future__ import annotations

import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.core.config import get_settings

_PUBLIC_PATHS = frozenset({"/health", "/ready"})


def extract_api_key(headers: Headers) -> str:
    header_key = headers.get("x-api-key", "").strip()
    if header_key:
        return header_key
    auth = headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def keys_match(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    # compare_digest requires equal length; unequal → reject without raising.
    if len(provided) != len(expected):
        return False
    return secrets.compare_digest(provided, expected)


class ApiKeyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in _PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        expected = get_settings().memory_api_key.strip()
        if not keys_match(extract_api_key(Headers(scope=scope)), expected):
            await JSONResponse({"detail": "Unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)
