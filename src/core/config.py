import re
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DEFAULT_OLLAMA_BASE = "http://localhost:11434"
MIN_API_KEY_LENGTH = 32
_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")


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
    # .env.local is read last and wins. Both files are git-ignored.
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://memory:memory@localhost:5432/agentic_memory"
    database_url_sync: str = "postgresql+psycopg://memory:memory@localhost:5432/agentic_memory"

    # Required. Every route except /health and /ready needs X-API-Key or Bearer.
    memory_api_key: str = ""

    # Off: no embedding call anywhere and no semantic_search tool. Keyword search stays.
    semantic_search_enabled: bool = False
    openai_api_key: str = ""
    litellm_api_base: str | None = None
    embedding_direct: bool = False
    embedding_api_base: str | None = None
    embedding_model: str = "ollama/bge-m3"
    embedding_dimensions: int = 1024

    # L4 rows live until this many months after their last touch.
    l4_idle_retention_months: int = 6
    # A touch writes at most once per this many days.
    l4_touch_min_days: int = 30

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: str = "INFO"
    # Extra Host header values accepted by /mcp (comma-separated), e.g. memory.example.com
    mcp_allowed_hosts: str = ""

    default_limit: int = 5
    max_search_limit: int = 5
    max_list_limit: int = 50
    sanitize_max_len: int = 1500
    sql_force_limit: int = 10
    # Write limits: one raw payload, and the content of one fact or note.
    max_raw_chars: int = 200_000
    max_text_chars: int = 20_000
    # get_raw_context pages: default and largest page.
    raw_page_chars: int = 8_000
    max_raw_page_chars: int = 20_000
    # Namespaces listed by get_project_overview.
    overview_namespace_limit: int = 20

    privacy_denylist_file: str = ".privacy-denylist"

    # NOLOGIN role that query_deep_memory_sql runs as: SELECT on l4_raw_events only.
    # bin/create-db-user.sh creates it and grants it to the service's database user.
    sql_reader_role: str = "agentic_memory_sql_reader"

    @field_validator("sql_reader_role")
    @classmethod
    def validate_role_name(cls, value: str) -> str:
        # Interpolated into SET ROLE and GRANT, so a plain identifier only.
        if not _ROLE_NAME.fullmatch(value):
            raise ValueError("SQL_READER_ROLE must match [a-z_][a-z0-9_]{0,62}")
        return value

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

    def require_api_key(self) -> None:
        """The service listens on HTTP only, so it never starts without a strong key."""
        key = self.memory_api_key.strip()
        if len(key) < MIN_API_KEY_LENGTH:
            raise RuntimeError(
                f"MEMORY_API_KEY must be set to at least {MIN_API_KEY_LENGTH} characters. "
                "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )

    def extra_mcp_hosts(self) -> list[str]:
        return [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
