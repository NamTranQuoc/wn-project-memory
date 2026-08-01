"""Integration tests against docker-compose Postgres. Skip if DB unavailable."""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.services import memory_service, search_service
from src.services.hashing import compute_source_hash

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://memory:memory@localhost:5432/memory_agent",
)


async def _db_available() -> bool:
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    if not await _db_available():
        pytest.skip("Postgres not available — run `make db-up && make migrate` first")

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess
        await sess.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_init_log_search_flow(session: AsyncSession) -> None:
    project = f"/tmp/test-project-{uuid.uuid4()}"

    init = await memory_service.init_project_memory(
        session, project, "Python FastAPI project with uv"
    )
    assert init["status"] == "initialized"

    fake_embedding = [0.01] * 1536

    with (
        patch(
            "src.services.distillation_service.chat_completion",
            new_callable=AsyncMock,
            return_value='{"facts":[{"entity_key":"stack","content":"Uses FastAPI and uv"}]}',
        ),
        patch(
            "src.services.distillation_service.embed_text",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ),
        patch(
            "src.services.search_service.embed_text",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ),
    ):
        from src.services import distillation_service

        event = await memory_service.log_raw_event(
            session,
            project_path=project,
            event_type="feedback",
            content="Decision: use FastAPI with uv for dependency management.",
            source_hash=compute_source_hash("Decision: use FastAPI with uv"),
        )
        distill = await distillation_service.distill_event_by_id(session, event["id"])
        assert distill["status"] == "processed"
        assert distill["facts_count"] >= 1

        # Direct L3 upsert path also works
        await memory_service.upsert_distilled_rule(
            session,
            project_path=project,
            entity_key="stack",
            content="Uses FastAPI and uv",
            raw_event_id=event["id"],
            source_hash=event["source_hash"],
            embedding=fake_embedding,
        )

        results = await search_service.search_memory(
            session,
            project_path=project,
            query="FastAPI",
            search_type="keyword",
            limit=5,
        )
        assert results["count"] >= 1
        assert any("FastAPI" in r["content"] for r in results["results"])

    await session.commit()
