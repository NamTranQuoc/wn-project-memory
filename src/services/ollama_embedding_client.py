"""Direct Ollama embedding calls (bypasses LiteLLM)."""

from __future__ import annotations

import logging

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.core.config import get_settings
from src.services.litellm_client import LLMTransientError
from src.services.secret_redactor import redact_secrets

logger = logging.getLogger(__name__)


def strip_provider_prefix(model: str) -> str:
    """Strip LiteLLM-style provider prefix: ``ollama/bge-m3`` -> ``bge-m3``."""
    if "/" in model:
        return model.split("/", 1)[1]
    return model


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 503}
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "rate limit",
            "rate_limit",
            "timeout",
            "connection",
            "429",
            "503",
        )
    )


@retry(
    retry=retry_if_exception_type(LLMTransientError),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def create_embedding(text: str, *, model: str | None = None) -> list[float]:
    settings = get_settings()
    safe_text = redact_secrets(text)
    ollama_model = strip_provider_prefix(model or settings.embedding_model)
    base = settings.resolved_embedding_api_base()
    url = f"{base}/api/embeddings"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                json={"model": ollama_model, "prompt": safe_text},
            )
            response.raise_for_status()
            payload = response.json()
        embedding = payload.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise ValueError(f"Ollama embedding response missing embedding: {payload!r}")
        return [float(x) for x in embedding]
    except Exception as exc:
        if _is_transient(exc):
            logger.warning("Transient Ollama embedding error, will retry: %s", exc)
            raise LLMTransientError(str(exc)) from exc
        raise
