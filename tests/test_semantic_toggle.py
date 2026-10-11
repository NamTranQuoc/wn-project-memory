import pytest
from mcp.server.mcpserver import MCPServer

from src.core.config import get_settings
from src.services import embedding


@pytest.fixture
def semantic(monkeypatch: pytest.MonkeyPatch):
    def set_flag(value: bool) -> None:
        monkeypatch.setattr(get_settings(), "semantic_search_enabled", value)

    return set_flag


async def _boom(*_args, **_kwargs):
    raise AssertionError("embedding client must not be called")


async def test_off_makes_no_call(semantic, monkeypatch: pytest.MonkeyPatch) -> None:
    semantic(False)
    monkeypatch.setattr(embedding.litellm_client, "create_embedding", _boom)
    monkeypatch.setattr(embedding.ollama_embedding_client, "create_embedding", _boom)
    assert await embedding.maybe_embed("anything") is None


async def test_on_embeds_redacted_text(semantic, monkeypatch: pytest.MonkeyPatch) -> None:
    semantic(True)
    monkeypatch.setattr(get_settings(), "embedding_direct", True)
    seen: list[str] = []

    async def fake(text: str, **_kwargs) -> list[float]:
        seen.append(text)
        return [0.1, 0.2]

    monkeypatch.setattr(embedding.ollama_embedding_client, "create_embedding", fake)
    token = "sk-" + "a" * 30
    assert await embedding.maybe_embed(f"key {token}") == [0.1, 0.2]
    assert token not in seen[0]


async def test_semantic_tool_only_when_registered() -> None:
    from src.mcp_server import mcp, register_semantic_tools

    names = {t.name for t in await mcp.list_tools()}
    assert "semantic_search" not in names
    assert "search_facts" in names

    other = MCPServer("probe")
    register_semantic_tools(other)
    assert {t.name for t in await other.list_tools()} == {"semantic_search"}
