"""Unit tests for REST API key gate and LiteLLM base URL validation."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.core.auth import ApiKeyMiddleware
from src.core.config import Settings, get_settings


def _app_with_auth() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiKeyMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/projects/demo/search")
    async def search() -> dict[str, str]:
        return {"ok": "yes"}

    return app


class TestLitellmApiBaseValidation:
    def test_localhost_http_allowed(self) -> None:
        s = Settings(litellm_api_base="http://localhost:4000")
        assert s.litellm_api_base == "http://localhost:4000"

    def test_loopback_http_allowed(self) -> None:
        s = Settings(litellm_api_base="http://127.0.0.1:4000/")
        assert s.litellm_api_base == "http://127.0.0.1:4000"

    def test_remote_https_allowed(self) -> None:
        s = Settings(litellm_api_base="https://proxy.example.com/v1")
        assert s.litellm_api_base == "https://proxy.example.com/v1"

    def test_remote_http_rejected(self) -> None:
        with pytest.raises(ValidationError, match="http is only allowed"):
            Settings(litellm_api_base="http://evil.example.com:4000")

    def test_empty_becomes_none(self) -> None:
        assert Settings(litellm_api_base="").litellm_api_base is None


class TestApiKeyMiddleware:
    def setup_method(self) -> None:
        get_settings.cache_clear()

    def teardown_method(self) -> None:
        get_settings.cache_clear()

    def test_open_when_key_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_API_KEY", "")
        get_settings.cache_clear()
        client = TestClient(_app_with_auth())
        assert client.get("/health").status_code == 200
        assert client.get("/projects/demo/search").status_code == 200

    def test_rejects_missing_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_API_KEY", "test-secret-key-value")
        get_settings.cache_clear()
        client = TestClient(_app_with_auth())
        assert client.get("/health").status_code == 200
        assert client.get("/projects/demo/search").status_code == 401

    def test_accepts_x_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_API_KEY", "test-secret-key-value")
        get_settings.cache_clear()
        client = TestClient(_app_with_auth())
        response = client.get(
            "/projects/demo/search",
            headers={"X-API-Key": "test-secret-key-value"},
        )
        assert response.status_code == 200
        assert response.json() == {"ok": "yes"}

    def test_accepts_bearer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_API_KEY", "test-secret-key-value")
        get_settings.cache_clear()
        client = TestClient(_app_with_auth())
        response = client.get(
            "/projects/demo/search",
            headers={"Authorization": "Bearer test-secret-key-value"},
        )
        assert response.status_code == 200

    def test_rejects_wrong_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_API_KEY", "test-secret-key-value")
        get_settings.cache_clear()
        client = TestClient(_app_with_auth())
        response = client.get(
            "/projects/demo/search",
            headers={"X-API-Key": "wrong-key-different-len"},
        )
        assert response.status_code == 401
