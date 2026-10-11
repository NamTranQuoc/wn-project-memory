"""The only path to an embedding call. With semantic search off, nothing leaves the process."""

from __future__ import annotations

from src.core.config import get_settings
from src.services import litellm_client, ollama_embedding_client
from src.services.secret_redactor import redact_secrets


def semantic_enabled() -> bool:
    return get_settings().semantic_search_enabled


async def embed_text(text: str) -> list[float]:
    """Always embeds. Callers outside this module use maybe_embed or check semantic_enabled."""
    clean = redact_secrets(text)
    if get_settings().embedding_direct:
        return await ollama_embedding_client.create_embedding(clean)
    return await litellm_client.create_embedding(clean)


async def maybe_embed(text: str) -> list[float] | None:
    if not semantic_enabled():
        return None
    return await embed_text(text)
