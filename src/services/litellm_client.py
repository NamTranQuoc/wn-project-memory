import logging
import os
from typing import Any

import litellm
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.core.config import get_settings

logger = logging.getLogger(__name__)


class LLMTransientError(Exception):
    """Raised for rate-limit / network errors that should be retried."""


def _ensure_api_key() -> None:
    settings = get_settings()
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)


@retry(
    retry=retry_if_exception_type(LLMTransientError),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    response_format: dict[str, Any] | None = None,
) -> str:
    _ensure_api_key()
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "model": model or settings.distillation_model,
        "messages": messages,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format

    try:
        response = await litellm.acompletion(**kwargs)
        return response.choices[0].message.content or ""
    except Exception as exc:
        msg = str(exc).lower()
        if any(
            token in msg
            for token in ("rate limit", "rate_limit", "timeout", "connection", "429", "503")
        ):
            logger.warning("Transient LLM error, will retry: %s", exc)
            raise LLMTransientError(str(exc)) from exc
        raise


@retry(
    retry=retry_if_exception_type(LLMTransientError),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def create_embedding(text: str, *, model: str | None = None) -> list[float]:
    _ensure_api_key()
    settings = get_settings()
    try:
        response = await litellm.aembedding(
            model=model or settings.embedding_model,
            input=[text],
        )
        return list(response.data[0]["embedding"])
    except Exception as exc:
        msg = str(exc).lower()
        if any(
            token in msg
            for token in ("rate limit", "rate_limit", "timeout", "connection", "429", "503")
        ):
            logger.warning("Transient embedding error, will retry: %s", exc)
            raise LLMTransientError(str(exc)) from exc
        raise
