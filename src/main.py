import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from src.core.auth import ApiKeyMiddleware, warn_if_rest_unauthenticated
from src.core.db import engine
from src.core.scheduler import start_scheduler, stop_scheduler
from src.routers import health, memory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Verify extensions exist (created by Alembic, but assert readiness)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except Exception:
        logger.exception("Could not verify Postgres extensions — is the database running?")

    warn_if_rest_unauthenticated()
    start_scheduler()
    logger.info("Memory service started")
    yield
    stop_scheduler()
    await engine.dispose()
    logger.info("Memory service stopped")


app = FastAPI(
    title="Agentic Memory Service",
    description="Hierarchical Agentic Memory Service (L0-L4) with MCP + REST",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(ApiKeyMiddleware)
app.include_router(health.router)
app.include_router(memory.router)
