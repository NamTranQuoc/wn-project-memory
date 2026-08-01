from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://memory:memory@localhost:5432/memory_agent"
    database_url_sync: str = "postgresql+psycopg://memory:memory@localhost:5432/memory_agent"

    openai_api_key: str = ""
    distillation_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    l4_retention_months: int = 6

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    default_search_limit: int = 5
    max_search_limit: int = 5
    sanitize_max_len: int = 1500
    sql_force_limit: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
