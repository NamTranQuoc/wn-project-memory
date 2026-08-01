from src.services.litellm_client import create_embedding


async def embed_text(text: str) -> list[float]:
    return await create_embedding(text)
