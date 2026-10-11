from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from src import models  # noqa: F401 — register every model on Base.metadata
from src.core.config import get_settings
from src.core.db import Base
from src.services.partition_service import PARTITION_NAME_RE

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url_sync.replace("%", "%%"))


def include_object(obj, name, type_, reflected, compare_to):
    """Monthly L4 partitions are managed by partition_service, not by migrations."""
    table = obj if type_ == "table" else getattr(obj, "table", None)
    table_name = getattr(table, "name", name) or ""
    return not PARTITION_NAME_RE.match(table_name)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
