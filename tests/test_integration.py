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
    except Exception:  # noqa: BLE001 — any failure means "not available", by design
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
    """Agent-driven flow: log_raw_event is a plain audit-trail append (no LLM
    distillation) — the caller (the agent) reads the raw content itself and
    writes the structured L3 rule directly, citing raw_event_id for provenance.
    """
    project = f"/tmp/test-project-{uuid.uuid4()}"

    init = await memory_service.init_project_memory(
        session, project, "Python FastAPI project with uv"
    )
    assert init["status"] == "initialized"

    event = await memory_service.log_raw_event(
        session,
        project_path=project,
        event_type="feedback",
        content="Decision: use FastAPI with uv for dependency management.",
        source_hash=compute_source_hash("Decision: use FastAPI with uv"),
    )
    assert "distillation_status" not in event

    fake_embedding = [0.01] * 1024

    # Agent extracts the fact itself and writes it directly — no distillation LLM call.
    await memory_service.upsert_distilled_rule(
        session,
        project_path=project,
        entity_key="stack",
        content="Uses FastAPI and uv",
        raw_event_id=event["id"],
        source_hash=event["source_hash"],
        embedding=fake_embedding,
    )

    with patch(
        "src.services.search_service.embed_text",
        new_callable=AsyncMock,
        return_value=fake_embedding,
    ):
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


@pytest.mark.asyncio
async def test_ops_layer_sources_tasks_facts_watermarks(session: AsyncSession) -> None:
    from src.services import (
        fact_service,
        source_service,
        task_service,
        watched_ref_service,
        watermark_service,
    )

    project = f"/tmp/ops-project-{uuid.uuid4()}"
    init = await memory_service.init_project_memory(
        session,
        project,
        "Ops layer smoke",
        sources=[
            {
                "source_key": "pr_1097",
                "source_type": "github_pr",
                "display_name": "War room PR",
                "connection_config": {
                    "repo": "TechX-Corp/synaptix-platform",
                    "number": 1097,
                },
                "read_recipe": "gh api repos/.../issues/1097/comments?since=...",
            }
        ],
    )
    assert init["status"] == "initialized"
    assert init["project_id"]
    assert any(s["source_key"] == "pr_1097" for s in init["sources_registered"])

    sources = await source_service.list_data_sources(session, project)
    keys = {s["source_key"] for s in sources["sources"]}
    assert "user_session" in keys
    assert "legacy_unattributed" in keys
    assert "pr_1097" in keys

    fake_embedding = [0.02] * 1024
    with (
        patch(
            "src.services.fact_service.embed_text",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ),
        patch(
            "src.services.task_service.embed_text",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ),
        patch(
            "src.services.watched_ref_service.embed_text",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ),
    ):
        fact = await fact_service.upsert_fact(
            session,
            project,
            fact_key="decision:freeze-active",
            kind="decision",
            title="Freeze active",
            content="PHA-1 reconcile freeze bars feature merges",
            priority=10,
            source_key="pr_1097",
        )
        assert fact["action"] == "created"
        assert fact["fact_key"] == "decision:freeze-active"
        assert fact["source_id"]

        task = await task_service.upsert_task(
            session,
            project,
            task_key="O-28",
            title="Ask Khanh Q1-Q5",
            content="Awaiting requalify seam answers",
            waiting_on="Khanh",
            priority=5,
        )
        assert task["task_key"] == "O-28"
        closed = await task_service.close_task(session, project, "O-28")
        assert closed["status"] == "closed"

        ref = await watched_ref_service.upsert_watched_ref(
            session,
            project,
            ref_type="pr",
            ref_value="1106",
            why="sre-agent phase1 branch",
            disposition="mine",
            source_key="pr_1097",
        )
        assert ref["ref_value"] == "1106"

    wm = await watermark_service.upsert_watermark(
        session,
        project,
        source_key="pr_1097",
        stream_key="comments",
        indexed_through={"updated_at": "2026-07-31T03:20:00Z", "id": 5116061513},
        full_read_ids=[5116061513],
        known_gaps=[],
    )
    assert wm["action"] == "created"
    got = await watermark_service.get_watermark(
        session, project, source_key="pr_1097", stream_key="comments"
    )
    assert got["indexed_through"]["id"] == 5116061513

    await session.commit()


@pytest.mark.asyncio
async def test_l1_reference_service_and_watched_ref_status_note(
    session: AsyncSession,
) -> None:
    from src.services import l1_reference_service, watched_ref_service

    project = f"/tmp/l1-ref-project-{uuid.uuid4()}"
    init = await memory_service.init_project_memory(session, project, "L1 reference smoke")
    assert init["status"] == "initialized"

    fake_embedding = [0.03] * 1024
    long_roster = "| Name | GitHub | Teams |\n| --- | --- | --- |\n| Nam | nq | n@x.com |\n" + (
        "x" * 2000
    )

    with patch(
        "src.services.l1_reference_service.embed_text",
        new_callable=AsyncMock,
        return_value=fake_embedding,
    ):
        created = await l1_reference_service.upsert_l1_reference(
            session,
            project,
            ref_key="roster",
            title="People & contact roster",
            content=long_roster,
            is_policy=False,
            priority=0,
        )
        assert created["action"] == "created"
        assert len(created["content"]) > 1500  # untruncated even on the upsert response

        overwritten = await l1_reference_service.upsert_l1_reference(
            session,
            project,
            ref_key="roster",
            title="People & contact roster",
            content="Updated roster content " + ("y" * 2000),
            is_policy=False,
            priority=0,
        )
        assert overwritten["action"] == "overwritten"
        assert overwritten["id"] == created["id"]

        unchanged = await l1_reference_service.upsert_l1_reference(
            session,
            project,
            ref_key="roster",
            title="People & contact roster",
            content="Updated roster content " + ("y" * 2000),
            is_policy=False,
            priority=0,
        )
        assert unchanged["action"] == "unchanged"

        policy = await l1_reference_service.upsert_l1_reference(
            session,
            project,
            ref_key="write-gate-policy",
            title="Project write-gate policy",
            content="Confirm before any push. " + ("z" * 2000),
            is_policy=True,
            priority=10,
        )
        assert policy["action"] == "created"
        assert policy["is_policy"] is True

    full = await l1_reference_service.get_l1_reference(session, project, "roster")
    assert len(full["content"]) > 1500
    assert "[truncated" not in full["content"]

    missing = await l1_reference_service.get_l1_reference(session, project, "does-not-exist")
    assert missing["error"] == "not_found"

    listed = await l1_reference_service.list_l1_references(session, project)
    assert listed["count"] == 2
    assert all("content" not in item for item in listed["references"])

    policies_only = await l1_reference_service.list_l1_references(
        session, project, policy_only=True
    )
    assert policies_only["count"] == 1
    assert policies_only["references"][0]["ref_key"] == "write-gate-policy"

    with patch(
        "src.services.l1_reference_service.embed_text",
        new_callable=AsyncMock,
        return_value=fake_embedding,
    ):
        search_results = await l1_reference_service.search_l1_references(
            session, project, "roster", search_type="keyword", limit=5
        )
        assert search_results["count"] >= 1

    active = await l1_reference_service.get_active_policies(session, project)
    assert active["count"] == 1
    assert len(active["policies"][0]["content"]) > 1500  # untruncated

    with patch(
        "src.services.watched_ref_service.embed_text",
        new_callable=AsyncMock,
        return_value=fake_embedding,
    ):
        ref = await watched_ref_service.upsert_watched_ref(
            session,
            project,
            ref_type="pr",
            ref_value="9999",
            why="tracking release branch",
            status_note="opened, awaiting review",
        )
        assert ref["why"] == "tracking release branch"
        assert ref["status_note"] == "opened, awaiting review"

        updated_ref = await watched_ref_service.upsert_watched_ref(
            session,
            project,
            ref_type="pr",
            ref_value="9999",
            status_note="MERGED to main 2026-08-02T00:53:12Z, squash 48a533d49",
        )
        assert updated_ref["why"] == "tracking release branch"  # unchanged
        assert updated_ref["status_note"] == "MERGED to main 2026-08-02T00:53:12Z, squash 48a533d49"

    await session.commit()


@pytest.mark.asyncio
async def test_source_unit_idempotent_ingest_and_checks(session: AsyncSession) -> None:
    from sqlalchemy import func, select

    from src.models import L4RawEvent
    from src.services import fact_service, source_service, source_unit_service, watermark_service

    project = f"/tmp/source-unit-{uuid.uuid4()}"
    await memory_service.init_project_memory(
        session,
        project,
        "Source unit smoke",
        sources=[
            {
                "source_key": "teams_war_room",
                "source_type": "teams_chat",
                "display_name": "War room",
                "connection_config": {"chat_id": "19:abc@thread.v2"},
                "read_recipe": "ms365 get-chat messages newest-first",
            },
            {
                "source_key": "repo_main",
                "source_type": "github_repo",
                "connection_config": {"repo": "org/app"},
                "read_recipe": "git rev-parse HEAD; git show <blob>",
            },
        ],
    )

    first = await source_unit_service.ingest_source_unit(
        session,
        project,
        source_key="teams_war_room",
        stream_key="messages",
        external_id="msg-100",
        content="Ship freeze stays until Friday",
    )
    assert first["action"] == "created"
    assert first["raw_event_id"]
    first_event_id = first["raw_event_id"]

    again = await source_unit_service.ingest_source_unit(
        session,
        project,
        source_key="teams_war_room",
        stream_key="messages",
        external_id="msg-100",
        content="Ship freeze stays until Friday",
    )
    assert again["action"] == "unchanged"
    assert again["raw_event_id"] == first_event_id

    count_result = await session.execute(
        select(func.count())
        .select_from(L4RawEvent)
        .where(L4RawEvent.project_path == project)
    )
    assert int(count_result.scalar_one()) == 1

    edited = await source_unit_service.ingest_source_unit(
        session,
        project,
        source_key="teams_war_room",
        stream_key="messages",
        external_id="msg-100",
        content="Ship freeze stays until Monday",
    )
    assert edited["action"] == "changed"
    assert edited["raw_event_id"] != first_event_id

    count_result = await session.execute(
        select(func.count())
        .select_from(L4RawEvent)
        .where(L4RawEvent.project_path == project)
    )
    assert int(count_result.scalar_one()) == 2

    twin = await source_unit_service.ingest_source_unit(
        session,
        project,
        source_key="teams_war_room",
        stream_key="messages",
        external_id="msg-101",
        content="Ship freeze stays until Monday",
    )
    assert twin["action"] == "created"
    assert twin["item_key"] != edited["item_key"]

    checked = await source_unit_service.check_source_units(
        session,
        project,
        source_key="teams_war_room",
        stream_key="messages",
        candidates=[
            {"external_id": "msg-999", "content": "brand new"},
            {
                "external_id": "msg-100",
                "content": "Ship freeze stays until Monday",
            },
            {
                "external_id": "msg-100",
                "content": "Ship freeze stays until Tuesday",
            },
        ],
    )
    assert checked["count"] == 3
    assert checked["results"][0]["status"] == "unknown"
    assert checked["results"][1]["status"] == "unchanged"
    assert checked["results"][2]["status"] == "changed"

    over_limit = await source_unit_service.check_source_units(
        session,
        project,
        source_key="teams_war_room",
        candidates=[{"external_id": f"x-{i}", "content": f"c-{i}"} for i in range(10)],
        limit=100,
    )
    assert over_limit["count"] == 5
    assert over_limit["limit_applied"] == 5

    git_hash = "a" * 40
    git_first = await source_unit_service.ingest_source_unit(
        session,
        project,
        source_key="repo_main",
        stream_key="files",
        external_id="path:README.md",
        content="# App\n",
        source_hash=git_hash,
    )
    assert git_first["action"] == "created"
    git_same = await source_unit_service.ingest_source_unit(
        session,
        project,
        source_key="repo_main",
        stream_key="files",
        external_id="path:README.md",
        content="# App\n",
        source_hash=git_hash,
    )
    assert git_same["action"] == "unchanged"
    git_changed = await source_unit_service.ingest_source_unit(
        session,
        project,
        source_key="repo_main",
        stream_key="files",
        external_id="path:README.md",
        content="# App\n\nUpdated\n",
        source_hash="b" * 40,
    )
    assert git_changed["action"] == "changed"

    # Newest-first page hits known boundary → watermark may advance to newest processed tip.
    newest_id = "msg-200"
    newest = await source_unit_service.ingest_source_unit(
        session,
        project,
        source_key="teams_war_room",
        stream_key="messages",
        external_id=newest_id,
        content="Latest status ping",
    )
    assert newest["action"] == "created"

    page_check = await source_unit_service.check_source_units(
        session,
        project,
        source_key="teams_war_room",
        stream_key="messages",
        candidates=[
            {"external_id": newest_id, "content": "Latest status ping"},
            {
                "external_id": "msg-100",
                "content": "Ship freeze stays until Monday",
            },
        ],
    )
    assert page_check["results"][0]["status"] == "unchanged"
    assert page_check["results"][1]["status"] == "unchanged"

    wm = await watermark_service.upsert_watermark(
        session,
        project,
        source_key="teams_war_room",
        stream_key="messages",
        indexed_through={"message_id": newest_id, "created_at": "2026-08-15T00:00:00Z"},
        full_read_ids=[newest_id, "msg-100"],
        known_gaps=[],
        raw_event_id=newest["raw_event_id"],
    )
    assert wm["action"] == "created"
    assert wm["indexed_through"]["message_id"] == newest_id

    # Failed/429 style: leave watermark alone and record gap — do not invent "now".
    gap_wm = await watermark_service.upsert_watermark(
        session,
        project,
        source_key="teams_war_room",
        stream_key="messages",
        indexed_through={"message_id": newest_id, "created_at": "2026-08-15T00:00:00Z"},
        full_read_ids=[newest_id, "msg-100"],
        known_gaps=[{"reason": "teams_429", "at": "2026-08-15T01:00:00Z"}],
    )
    assert gap_wm["action"] == "updated"
    assert gap_wm["indexed_through"]["message_id"] == newest_id
    assert gap_wm["known_gaps"][0]["reason"] == "teams_429"

    fake_embedding = [0.04] * 1024
    with patch(
        "src.services.fact_service.embed_text",
        new_callable=AsyncMock,
        return_value=fake_embedding,
    ):
        fact = await fact_service.upsert_fact(
            session,
            project,
            fact_key="decision:freeze-window",
            kind="decision",
            title="Freeze window",
            content="Ship freeze stays until Monday",
            source_key="teams_war_room",
            raw_event_id=edited["raw_event_id"],
            source_hash=edited["source_hash"],
        )
        assert fact["action"] == "created"
        assert fact["fact_key"] == "decision:freeze-window"
        assert fact["raw_event_id"] == edited["raw_event_id"]
        assert fact["source_hash"] == edited["source_hash"]

    sources = await source_service.list_data_sources(session, project)
    assert {s["source_key"] for s in sources["sources"]} >= {
        "user_session",
        "legacy_unattributed",
        "teams_war_room",
        "repo_main",
    }

    await session.commit()


@pytest.mark.asyncio
async def test_query_deep_memory_sql_fallback_when_project_path_not_selected(
    session: AsyncSession,
) -> None:
    """A SELECT that doesn't project project_path fails the first (subquery-wrapped)
    attempt; the fallback must roll back before retrying on the same session, or
    Postgres rejects the retry with InFailedSQLTransactionError.
    """
    from src.services.sql_service import query_deep_memory_sql

    project = f"/tmp/sql-fallback-project-{uuid.uuid4()}"
    await memory_service.init_project_memory(session, project, "sql fallback smoke")
    await memory_service.log_raw_event(
        session,
        project_path=project,
        event_type="feedback",
        content="hello world",
        source_hash=compute_source_hash("hello world"),
    )
    await session.commit()

    result = await query_deep_memory_sql(
        session,
        project_path=project,
        sql_query="SELECT event_type, raw_content FROM l4_raw_events",
    )
    assert result["count"] == 1
    assert result["rows"][0]["event_type"] == "feedback"

    await session.commit()


@pytest.mark.asyncio
async def test_fact_key_overwrite_and_unchanged(session: AsyncSession) -> None:
    from src.services import fact_service

    project = f"/tmp/fact-key-project-{uuid.uuid4()}"
    await memory_service.init_project_memory(session, project, "fact key smoke")
    fake_embedding = [0.05] * 1024
    with patch(
        "src.services.fact_service.embed_text",
        new_callable=AsyncMock,
        return_value=fake_embedding,
    ):
        created = await fact_service.upsert_fact(
            session,
            project,
            fact_key="decision:deploy-freeze",
            kind="decision",
            title="Freeze",
            content="Freeze is on",
            source_key="user_session",
        )
        assert created["action"] == "created"
        assert created["fact_key"] == "decision:deploy-freeze"
        fact_id = created["id"]

        overwritten = await fact_service.upsert_fact(
            session,
            project,
            fact_key="decision:deploy-freeze",
            kind="decision",
            title="Freeze",
            content="Freeze is lifted",
            source_key="user_session",
        )
        assert overwritten["action"] == "overwritten"
        assert overwritten["id"] == fact_id
        assert "lifted" in overwritten["content"]

        unchanged = await fact_service.upsert_fact(
            session,
            project,
            fact_key="decision:deploy-freeze",
            kind="decision",
            title="Freeze",
            content="Freeze is lifted",
            source_key="user_session",
        )
        assert unchanged["action"] == "unchanged"
        assert unchanged["id"] == fact_id

        got = await fact_service.get_fact(session, project, "decision:deploy-freeze")
        assert got["id"] == fact_id
        deleted = await fact_service.delete_fact(session, project, "decision:deploy-freeze")
        assert deleted["action"] == "deleted"
        missing = await fact_service.get_fact(session, project, "decision:deploy-freeze")
        assert missing["error"] == "not_found"

    await session.commit()


@pytest.mark.asyncio
async def test_external_reindex_reset_preserves_curated(session: AsyncSession) -> None:
    from src.services import (
        fact_service,
        reindex_service,
        source_service,
        source_unit_service,
        task_service,
        watched_ref_service,
        watermark_service,
    )

    project = f"/tmp/reindex-project-{uuid.uuid4()}"
    await memory_service.init_project_memory(session, project, "reindex smoke")
    await source_service.register_data_source(
        session,
        project,
        source_key="teams_war_room",
        source_type="teams_chat",
        display_name="War room",
        connection_config={"chat_id": "abc"},
        read_recipe="list messages",
        added_via="manual",
    )
    await source_service.register_data_source(
        session,
        project,
        source_key="local_plans",
        source_type="local_file",
        display_name="Local plans",
        connection_config={"path": "/tmp/plans"},
        read_recipe="read files",
        added_via="manual",
    )

    fake_embedding = [0.06] * 1024
    with (
        patch(
            "src.services.fact_service.embed_text",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ),
        patch(
            "src.services.task_service.embed_text",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ),
        patch(
            "src.services.watched_ref_service.embed_text",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ),
    ):
        unit = await source_unit_service.ingest_source_unit(
            session,
            project,
            source_key="teams_war_room",
            content="Decision from Teams: freeze Monday",
            stream_key="messages",
            external_id="msg-1",
        )
        assert unit["action"] == "created"

        await fact_service.upsert_fact(
            session,
            project,
            fact_key="decision:teams-freeze",
            kind="decision",
            title="Teams freeze",
            content="Freeze Monday",
            source_key="teams_war_room",
            raw_event_id=unit["raw_event_id"],
            source_hash=unit["source_hash"],
        )
        await memory_service.upsert_distilled_rule(
            session,
            project,
            entity_key="rule:teams-freeze",
            content="Freeze Monday from Teams",
            raw_event_id=unit["raw_event_id"],
            source_hash=unit["source_hash"],
            source_key="teams_war_room",
            embedding=fake_embedding,
        )
        await watermark_service.upsert_watermark(
            session,
            project,
            source_key="teams_war_room",
            stream_key="messages",
            indexed_through={"message_id": "msg-1"},
            full_read_ids=["msg-1"],
        )
        curated = await fact_service.upsert_fact(
            session,
            project,
            fact_key="decision:session-chot",
            kind="decision",
            title="Curated",
            content="User-session decision",
            source_key="user_session",
        )
        task = await task_service.upsert_task(
            session,
            project,
            task_key="O-99",
            title="Keep me",
            content="Protected task",
        )
        ref = await watched_ref_service.upsert_watched_ref(
            session,
            project,
            ref_type="pr",
            ref_value="1234",
            why="keep watched",
            disposition="mine",
        )

    with pytest.raises(ValueError, match="Protected"):
        await reindex_service.preview_external_reindex(
            session, project, source_keys=["user_session"]
        )
    with pytest.raises(ValueError, match="Protected"):
        await reindex_service.preview_external_reindex(
            session, project, source_keys=["local_plans"]
        )
    with pytest.raises(ValueError, match="confirm=true"):
        await reindex_service.apply_external_reindex_reset(
            session, project, source_keys=["teams_war_room"], confirm=False
        )

    preview = await reindex_service.preview_external_reindex(
        session, project, source_keys=["teams_war_room"]
    )
    assert preview["mode"] == "preview"
    assert preview["totals"]["facts"] >= 1
    assert preview["preserved"]["tasks"] >= 1

    applied = await reindex_service.apply_external_reindex_reset(
        session, project, source_keys=["teams_war_room"], confirm=True
    )
    assert applied["mode"] == "applied"
    assert applied["deleted"]["facts"] >= 1
    assert applied["deleted"]["source_units"] >= 1

    assert (await fact_service.get_fact(session, project, "decision:teams-freeze"))[
        "error"
    ] == "not_found"
    kept = await fact_service.get_fact(session, project, "decision:session-chot")
    assert kept["id"] == curated["id"]
    tasks = await task_service.list_tasks(session, project)
    assert any(t["task_key"] == "O-99" for t in tasks["tasks"])
    refs = await watched_ref_service.list_watched_refs(session, project)
    assert any(r["ref_value"] == "1234" for r in refs["watched_refs"])
    assert task["task_key"] == "O-99"
    assert ref["ref_value"] == "1234"

    inventory = await reindex_service.inventory_legacy_state(session, project, limit=5)
    assert inventory["legacy_source_key"] == "legacy_unattributed"

    await session.commit()
