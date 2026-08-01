import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from src.core.db import engine
from src.core.scheduler import start_scheduler, stop_scheduler
from src.routers import events_stream, health, memory

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
        logger.exception(
            "Could not verify Postgres extensions — is the database running?"
        )

    start_scheduler()
    logger.info("Memory service started")
    yield
    stop_scheduler()
    await engine.dispose()
    logger.info("Memory service stopped")


app = FastAPI(
    title="Agentic Memory Service",
    description="Hierarchical Agentic Memory Service (L1-L4) with MCP + REST/SSE",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(memory.router)
app.include_router(events_stream.router)
