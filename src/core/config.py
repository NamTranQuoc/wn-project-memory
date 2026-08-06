from functools import lru_cache
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DEFAULT_OLLAMA_BASE = "http://localhost:11434"


def _empty_str_to_none(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _validate_http_base_url(value: str | None, *, env_name: str) -> str | None:
    """Credential-trustee URLs — only https, or http on loopback."""
    if value is None:
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{env_name} must use http or https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"{env_name} must include a hostname")
    if parsed.scheme == "http" and host not in _LOCAL_HTTP_HOSTS:
        raise ValueError(
            f"{env_name} with http is only allowed for "
            "localhost / 127.0.0.1 / ::1; use https for remote endpoints"
        )
    return value.strip().rstrip("/")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://memory:memory@localhost:5432/memory_agent"
    database_url_sync: str = "postgresql+psycopg://memory:memory@localhost:5432/memory_agent"

    openai_api_key: str = ""
    litellm_api_base: str | None = None
    embedding_direct: bool = False
    embedding_api_base: str | None = None
    embedding_model: str = "ollama/bge-m3"
    embedding_dimensions: int = 1024

    # When set, REST requires X-API-Key or Authorization: Bearer. MCP stdio ignores this.
    memory_api_key: str = ""

    l4_retention_months: int = 6

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    default_search_limit: int = 5
    max_search_limit: int = 5
    sanitize_max_len: int = 1500
    sql_force_limit: int = 10

    @field_validator("litellm_api_base", "embedding_api_base", mode="before")
    @classmethod
    def empty_base_to_none(cls, value: object) -> object:
        return _empty_str_to_none(value)

    @field_validator("litellm_api_base")
    @classmethod
    def validate_litellm_api_base(cls, value: str | None) -> str | None:
        return _validate_http_base_url(value, env_name="LITELLM_API_BASE")

    @field_validator("embedding_api_base")
    @classmethod
    def validate_embedding_api_base(cls, value: str | None) -> str | None:
        return _validate_http_base_url(value, env_name="EMBEDDING_API_BASE")

    @model_validator(mode="after")
    def default_embedding_base_when_direct(self) -> "Settings":
        """When direct and no base configured, default to local Ollama."""
        if self.embedding_direct and not self.embedding_api_base and not self.litellm_api_base:
            self.embedding_api_base = _DEFAULT_OLLAMA_BASE
        return self

    def resolved_embedding_api_base(self) -> str:
        """Base URL for direct Ollama embedding calls."""
        return self.embedding_api_base or self.litellm_api_base or _DEFAULT_OLLAMA_BASE


@lru_cache
def get_settings() -> Settings:
    return Settings()
