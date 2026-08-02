from functools import lru_cache
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


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
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

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

    @field_validator("litellm_api_base", mode="before")
    @classmethod
    def empty_litellm_base_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("litellm_api_base")
    @classmethod
    def validate_litellm_api_base(cls, value: str | None) -> str | None:
        """Proxy URL is a credential trustee — only https, or http on loopback."""
        if value is None:
            return None
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("LITELLM_API_BASE must use http or https")
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValueError("LITELLM_API_BASE must include a hostname")
        if parsed.scheme == "http" and host not in _LOCAL_HTTP_HOSTS:
            raise ValueError(
                "LITELLM_API_BASE with http is only allowed for "
                "localhost / 127.0.0.1 / ::1; use https for remote proxies"
            )
        return value.strip().rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
