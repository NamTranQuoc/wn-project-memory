import logging
import os
from typing import Any

# litellm fetches a public model-pricing JSON from GitHub on import unless this is
# set — we never use its cost-tracking features, so force the bundled local copy
# instead of a network call every process start. Must be set before `import litellm`.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.core.config import get_settings
from src.services.secret_redactor import redact_secrets

logger = logging.getLogger(__name__)


class LLMTransientError(Exception):
    """Raised for rate-limit / network errors that should be retried."""


def _ensure_api_key() -> None:
    settings = get_settings()
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)


def _provider_kwargs() -> dict[str, Any]:
    """Route LiteLLM SDK calls through a local/proxy base URL when configured."""
    settings = get_settings()
    kwargs: dict[str, Any] = {}
    if settings.litellm_api_base:
        kwargs["api_base"] = settings.litellm_api_base
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    return kwargs


@retry(
    retry=retry_if_exception_type(LLMTransientError),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def create_embedding(text: str, *, model: str | None = None) -> list[float]:
    _ensure_api_key()
    settings = get_settings()
    safe_text = redact_secrets(text)
    try:
        response = await litellm.aembedding(
            model=model or settings.embedding_model,
            input=[safe_text],
            **_provider_kwargs(),
        )
        return list(response.data[0]["embedding"])
    except Exception as exc:
        msg = str(exc).lower()
        if any(
            token in msg
            for token in (
                "rate limit",
                "rate_limit",
                "timeout",
                "connection",
                "429",
                "503",
            )
        ):
            logger.warning("Transient embedding error, will retry: %s", exc)
            raise LLMTransientError(str(exc)) from exc
        raise
