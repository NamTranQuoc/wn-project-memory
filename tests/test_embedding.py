"""Unit tests for embedding routing and Ollama direct client helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.config import get_settings
from src.services.embedding_service import embed_text
from src.services.ollama_embedding_client import strip_provider_prefix


class TestStripProviderPrefix:
    def test_strips_ollama_prefix(self) -> None:
        assert strip_provider_prefix("ollama/bge-m3") == "bge-m3"

    def test_keeps_bare_model(self) -> None:
        assert strip_provider_prefix("bge-m3") == "bge-m3"

    def test_only_first_slash(self) -> None:
        assert strip_provider_prefix("ollama/org/model") == "org/model"


class TestEmbedTextRouting:
    def setup_method(self) -> None:
        get_settings.cache_clear()

    def teardown_method(self) -> None:
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_routes_to_ollama_when_direct(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_DIRECT", "true")
        monkeypatch.setenv("EMBEDDING_API_BASE", "http://localhost:11434")
        get_settings.cache_clear()
        fake = [0.1, 0.2, 0.3]
        with (
            patch(
                "src.services.embedding_service.ollama_create_embedding",
                new_callable=AsyncMock,
                return_value=fake,
            ) as ollama_mock,
            patch(
                "src.services.embedding_service.litellm_create_embedding",
                new_callable=AsyncMock,
            ) as litellm_mock,
        ):
            result = await embed_text("hello")
        assert result == fake
        ollama_mock.assert_awaited_once_with("hello")
        litellm_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_routes_to_litellm_when_not_direct(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_DIRECT", "false")
        get_settings.cache_clear()
        fake = [0.4, 0.5]
        with (
            patch(
                "src.services.embedding_service.ollama_create_embedding",
                new_callable=AsyncMock,
            ) as ollama_mock,
            patch(
                "src.services.embedding_service.litellm_create_embedding",
                new_callable=AsyncMock,
                return_value=fake,
            ) as litellm_mock,
        ):
            result = await embed_text("hello")
        assert result == fake
        litellm_mock.assert_awaited_once_with("hello")
        ollama_mock.assert_not_called()


class TestOllamaCreateEmbedding:
    def setup_method(self) -> None:
        get_settings.cache_clear()

    def teardown_method(self) -> None:
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_posts_native_embeddings_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_DIRECT", "true")
        monkeypatch.setenv("EMBEDDING_API_BASE", "http://localhost:11434")
        monkeypatch.setenv("EMBEDDING_MODEL", "ollama/bge-m3")
        get_settings.cache_clear()

        fake_vec = [0.01] * 4
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"embedding": fake_vec}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "src.services.ollama_embedding_client.httpx.AsyncClient",
            return_value=mock_client,
        ):
            from src.services.ollama_embedding_client import create_embedding

            result = await create_embedding("hello world")

        assert result == fake_vec
        mock_client.post.assert_awaited_once_with(
            "http://localhost:11434/api/embeddings",
            json={"model": "bge-m3", "prompt": "hello world"},
        )
