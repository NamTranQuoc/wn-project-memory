import secrets

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.core.auth import ApiKeyMiddleware
from src.core.config import Settings, get_settings


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiKeyMiddleware)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/mcp")
    async def mcp() -> dict:
        return {"ok": True}

    @app.get("/l3/search")
    async def search() -> dict:
        return {"ok": True}

    return app


class TestApiKeyRequired:
    def test_missing_key_refuses_start(self) -> None:
        with pytest.raises(RuntimeError, match="MEMORY_API_KEY"):
            Settings(memory_api_key="").require_api_key()

    def test_short_key_refuses_start(self) -> None:
        with pytest.raises(RuntimeError):
            Settings(memory_api_key="short").require_api_key()

    def test_strong_key_accepted(self) -> None:
        Settings(memory_api_key=secrets.token_urlsafe(32)).require_api_key()


class TestMiddleware:
    def test_health_is_public(self) -> None:
        assert TestClient(_app()).get("/health").status_code == 200

    def test_rest_and_mcp_need_key(self) -> None:
        client = TestClient(_app())
        assert client.get("/l3/search").status_code == 401
        assert client.post("/mcp").status_code == 401

    def test_wrong_key(self) -> None:
        client = TestClient(_app())
        assert client.get("/l3/search", headers={"X-API-Key": "nope"}).status_code == 401

    def test_header_and_bearer(self) -> None:
        key = get_settings().memory_api_key
        client = TestClient(_app())
        assert client.get("/l3/search", headers={"X-API-Key": key}).status_code == 200
        assert client.post("/mcp", headers={"Authorization": f"Bearer {key}"}).status_code == 200


class TestOutboundUrls:
    def test_loopback_http_allowed(self) -> None:
        assert Settings(litellm_api_base="http://127.0.0.1:4000/").litellm_api_base == (
            "http://127.0.0.1:4000"
        )

    def test_remote_http_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings(embedding_api_base="http://embeddings.example.com")

    def test_remote_https_allowed(self) -> None:
        assert Settings(embedding_api_base="https://embeddings.example.com").embedding_api_base


class TestMcpHosts:
    def test_extra_hosts_parsed(self) -> None:
        s = Settings(mcp_allowed_hosts=" memory.example.com , ,api.example.org")
        assert s.extra_mcp_hosts() == ["memory.example.com", "api.example.org"]
