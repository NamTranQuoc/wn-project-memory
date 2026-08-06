from src.core.config import get_settings
from src.services.litellm_client import create_embedding as litellm_create_embedding
from src.services.ollama_embedding_client import create_embedding as ollama_create_embedding


async def embed_text(text: str) -> list[float]:
    if get_settings().embedding_direct:
        return await ollama_create_embedding(text)
    return await litellm_create_embedding(text)
