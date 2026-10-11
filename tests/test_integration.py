"""End-to-end flows against a real Postgres (pgvector + pg_trgm, migrations applied).

Run with TEST_DATABASE_URL=postgresql+asyncpg://…/<throwaway db>. Every test starts
from empty tables.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from src.core.config import get_settings
from src.services.partition_service import partition_ddl
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")

P = "/work/payments"
OTHER = "/work/portal"
KEY = "decision.api.payment.idempotency"


@pytest.fixture
async def session():
    from src.core.db import AsyncSessionLocal, engine

    async with engine.begin() as conn:
        tables = (
            "g_notes, g_team_members, g_teams, g_identities, g_people, ingest_watermarks, "
            "ingest_units, l4_raw_events, l3_links, l3_revision_sources, l3_revisions, l3_facts, "
            "l2_namespaces, l1_sources, l1_notes, l1_projects"
        )
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    async with AsyncSessionLocal() as s:
        yield s
        await s.rollback()


async def _project_with_namespace(s) -> None:
    from src.services import l1_service, namespace_service

    await l1_service.set_overview(s, P, overview="Payment gateway for the shop")
    await l1_service.upsert_source(s, P, source_key="jira", source_type="jira",
                                   url="https://jira.example.com/browse/PAY")
    await namespace_service.upsert_namespace(
        s, P, namespace="decision.api", key_template="decision.api.{service}.{topic}",
        summary="API contract choices between services", default_kind="decision",
        keywords=["api", "contract"],
    )


async def _write(s, change: str, base: int, content: str, **extra):
    from src.services import fact_service

    return await fact_service.write_fact(
        s, P, fact_key=KEY, change_type=change, base_revision=base, title="Idempotency key",
        summary=content[:80], content=content, reason=f"{change} test", **extra,
    )


async def test_overview_counts_and_policy(session) -> None:
    from src.services import l1_service

    await _project_with_namespace(session)
    note = {"note_key": "policy.write-gate", "title": "Write gate",
            "content": "Confirm before posting.", "is_policy": True}
    planned = await l1_service.plan_note(session, P, **note)
    assert planned.needs_approval
    with pytest.raises(l1_service.PolicyApprovalRequired):
        await l1_service.upsert_note(session, P, **note)
    saved = await l1_service.upsert_note(session, P, **note, approved_via="mcp_elicitation")
    assert saved["approved_via"] == "mcp_elicitation" and saved["approved_at"]
    # Same text again: nothing to approve. Demoting the policy: the user decides.
    assert not (await l1_service.plan_note(session, P, **note)).needs_approval
    assert (await l1_service.plan_note(session, P, **{**note, "is_policy": False})).needs_approval
    # A plain note never asks.
    await l1_service.upsert_note(session, P, note_key="build", title="Build", content="make")

    overview = await l1_service.get_overview(session, P)
    assert overview["counts"] == {"policies": 1, "notes": 1, "sources": 2, "namespaces": 1}
    assert overview["policies"][0]["content"] == "Confirm before posting."
    assert overview["namespaces"][0]["key_template"] == "decision.api.{service}.{topic}"


async def test_write_by_namespace_and_batch(session) -> None:
    from src.services import fact_service

    await _project_with_namespace(session)
    base = {"change_type": "create", "title": "t", "summary": "s", "reason": "r"}
    one = await fact_service.write_fact(
        session, P, namespace="decision.api",
        parts={"service": "Payment", "topic": "Idempotency"}, content="first", **base,
    )
    assert one["fact_key"] == KEY and one["revision"] == 1
    batch = await fact_service.write_facts(session, P, items=[
        {"namespace": "decision.api", "parts": {"service": "payment", "topic": "retry"},
         "content": "retry", **base},
        {"fact_key": KEY, "content": "again", **base},
        {"fact_key": "random.key", "content": "x", **base},
        {"nope": 1},
    ])
    assert batch["written"] == 1
    errors = [item.get("error") for item in batch["items"]]
    assert errors == [None, "revision_conflict", "key_rule", "invalid"]
    assert batch["items"][1]["fact"]["content"] == "first"
    with pytest.raises(ValueError, match="at most 10"):
        await fact_service.write_facts(session, P, items=[{}] * 11)


async def test_raw_pages_and_size_limit(session) -> None:
    from src.services import raw_service

    await _project_with_namespace(session)
    body = "".join(f"{i:05d}" for i in range(4000))  # 20000 chars
    logged = await raw_service.log_raw_event(session, P, event_type="chat", content=body)
    first = await raw_service.get_raw_context(session, P, logged["raw_event_id"])
    assert (first["total_chars"], first["next_offset"]) == (20000, 8000)
    assert first["raw_content"] == body[:8000]
    last = await raw_service.get_raw_context(session, P, logged["raw_event_id"],
                                             offset=16000, max_chars=99999)
    assert last["raw_content"] == body[16000:] and last["next_offset"] is None
    with pytest.raises(ValueError, match="the limit is 200000"):
        await raw_service.log_raw_event(session, P, event_type="chat", content="x" * 200_001)


async def test_key_rule_on_write(session) -> None:
    from src.services import fact_service, namespace_service

    await _project_with_namespace(session)
    built = await namespace_service.build_fact_key(
        session, P, namespace="decision.api", parts={"service": "Payment", "topic": "Idempotency"}
    )
    assert built["fact_key"] == KEY
    with pytest.raises(namespace_service.KeyRuleError):
        await fact_service.write_fact(
            session, P, fact_key="random.key", change_type="create", base_revision=0,
            title="t", summary="s", content="c", reason="r",
        )


async def test_revisions_conflict_and_history(session) -> None:
    from src.services import fact_service, namespace_service, raw_service

    await _project_with_namespace(session)
    raw = await raw_service.log_raw_event(session, P, event_type="pr_comment",
                                          content="Add a 24h expiry", source_key="jira")
    created = await _write(session, "create", 0, "POST /charge needs Idempotency-Key")
    assert (created["action"], created["revision"]) == ("create", 1)

    with pytest.raises(fact_service.RevisionConflict) as conflict:
        await _write(session, "merge", 0, "stale write")
    assert conflict.value.current == 1

    merged = await _write(
        session, "merge", 1, "POST /charge needs Idempotency-Key; keys expire after 24h",
        sources=[{"source_key": "jira", "source_url": "https://jira.example.com/browse/PAY-88",
                  "raw_event_id": raw["raw_event_id"]}],
    )
    assert merged["revision"] == 2
    same = await _write(session, "merge", 2,
                        "POST /charge needs Idempotency-Key; keys expire after 24h")
    assert same["action"] == "unchanged"

    history = await fact_service.get_fact_history(session, P, KEY)
    assert [i["revision"] for i in history["items"]] == [2, 1]
    assert history["items"][0]["sources"][0]["source_url"].endswith("PAY-88")
    assert history["items"][1]["content"] == "POST /charge needs Idempotency-Key"

    index = await namespace_service.list_fact_index(session, P, namespace="decision.api")
    assert index["items"][0]["fact_key"] == KEY


async def test_keyword_search(session) -> None:
    from src.services import search_service

    await _project_with_namespace(session)
    await _write(session, "create", 0, "POST /charge needs Idempotency-Key")
    found = await search_service.search_facts(session, P, "idempotency")
    assert found["facts"][0]["fact_key"] == KEY
    assert "content" not in found["facts"][0]
    assert found["namespaces"] == [] or found["namespaces"][0]["namespace"] == "decision.api"
    ns = await search_service.search_facts(session, P, "contract")
    assert ns["namespaces"][0]["namespace"] == "decision.api"


async def test_full_text_search_ranks_all_terms_first(session) -> None:
    from src.services import fact_service, search_service

    await _project_with_namespace(session)
    await _write(session, "create", 0, "POST /charge needs Idempotency-Key; keys expire after 24h")
    await fact_service.write_fact(
        session, P, fact_key="decision.api.payments.retry", change_type="create",
        base_revision=0, title="Retry policy", summary="Retries back off for 24h",
        content="Charge retries back off exponentially", reason="test",
    )
    # Words in any order, prefixes, none of it a substring of one field.
    found = await search_service.search_facts(session, P, "expire idempot charge")
    assert found["facts"][0]["fact_key"] == KEY
    assert {f["fact_key"] for f in found["facts"]} == {KEY, "decision.api.payments.retry"}
    by_key = await search_service.search_facts(session, P, "payments retry")
    assert by_key["facts"][0]["fact_key"] == "decision.api.payments.retry"
    typo = await search_service.search_facts(session, P, "Retry polcy")
    assert typo["facts"][0]["fact_key"] == "decision.api.payments.retry"
    with pytest.raises(ValueError, match="no searchable words"):
        await search_service.search_facts(session, P, "%%")


async def test_curated_text_is_redacted(session) -> None:
    from src.services import fact_service

    await _project_with_namespace(session)
    token = "ghp_" + "a" * 36
    out = await _write(session, "create", 0, f"Deploy uses DEPLOY_TOKEN={token}; token: required")
    assert token not in out["content"]
    assert "token: required" in out["content"]
    again = await _write(session, "replace", 1, f"Deploy uses DEPLOY_TOKEN={token}; token: required")
    assert again["action"] == "unchanged"
    assert (await fact_service.get_fact(session, P, KEY))["revision"] == 1


async def test_raw_touch_and_dropped_fallback(session) -> None:
    from src.services import raw_service

    await _project_with_namespace(session)
    raw = await raw_service.log_raw_event(session, P, event_type="chat", content="hello",
                                          source_key="jira")
    rid = raw["raw_event_id"]
    await session.execute(text(partition_ddl(datetime.now(timezone.utc) - timedelta(days=40))))
    await session.execute(
        text("UPDATE l4_raw_events SET last_touched_at = now() - interval '40 days' WHERE id = :i"),
        {"i": rid},
    )
    read = await raw_service.get_raw_context(session, P, rid)
    assert read["raw_content"] == "hello"
    moved = (await session.execute(
        text("SELECT last_touched_at > now() - interval '1 day' FROM l4_raw_events WHERE id = :i"),
        {"i": rid},
    )).scalar_one()
    assert moved is True

    await _write(session, "create", 0, "value",
                 sources=[{"source_key": "jira", "source_url": "https://jira.example.com/x",
                           "raw_event_id": rid}])
    await session.execute(text("DELETE FROM l4_raw_events WHERE id = :i"), {"i": rid})
    gone = await raw_service.get_raw_context(session, P, rid)
    assert gone["error"] == "raw_dropped"
    assert gone["source_urls"] == ["https://jira.example.com/x"]


async def test_ingest_is_idempotent_and_reset_keeps_facts(session) -> None:
    from src.services import fact_service, ingest_service

    await _project_with_namespace(session)
    first = await ingest_service.ingest_source_unit(session, P, source_key="jira",
                                                    content="PAY-88 body", external_id="PAY-88")
    again = await ingest_service.ingest_source_unit(session, P, source_key="jira",
                                                    content="PAY-88 body", external_id="PAY-88")
    changed = await ingest_service.ingest_source_unit(session, P, source_key="jira",
                                                      content="PAY-88 edited", external_id="PAY-88")
    assert [first["action"], again["action"], changed["action"]] == ["created", "unchanged", "changed"]
    check = await ingest_service.check_source_units(
        session, P, source_key="jira",
        candidates=[{"external_id": "PAY-88", "content": "PAY-88 edited"}, {"external_id": "PAY-1"}],
    )
    assert [r["status"] for r in check["results"]] == ["unchanged", "unknown"]

    await _write(session, "create", 0, "value")
    preview = await ingest_service.reset_source_ingest(session, P, source_key="jira")
    assert preview["action"] == "preview"
    done = await ingest_service.reset_source_ingest(session, P, source_key="jira", confirm=True)
    assert done["units_deleted"] == 1
    assert (await fact_service.get_fact(session, P, KEY))["revision"] == 1


async def test_sql_tool_is_scoped(session) -> None:
    from src.services import raw_service, sql_service

    await _project_with_namespace(session)
    await raw_service.log_raw_event(session, P, event_type="mine", content="payments row")
    await raw_service.log_raw_event(session, OTHER, event_type="theirs", content="portal row")
    await session.commit()
    out = await sql_service.query_deep_memory_sql(
        session, P, "SELECT event_type FROM l4_raw_events ORDER BY created_at")
    await session.rollback()
    assert [r["event_type"] for r in out["rows"]] == ["mine"]
    with pytest.raises(ValueError):
        await sql_service.query_deep_memory_sql(session, P, "SELECT * FROM g_people")
    await session.rollback()
    with pytest.raises(ValueError, match="Only l4_raw_events"):
        await sql_service.query_deep_memory_sql(
            session, P,
            "SELECT e.id FROM l4_raw_events e WHERE e.id IN (SELECT id FROM l1_projects)",
        )
    await session.rollback()
    with pytest.raises(ValueError, match="permission denied"):
        await sql_service.query_deep_memory_sql(session, P, "SELECT 1 FROM l4_raw_events, l3_facts")
    await session.rollback()
    who = await sql_service.query_deep_memory_sql(
        session, P, "SELECT current_user AS who FROM l4_raw_events")
    await session.rollback()
    assert who["rows"][0]["who"] == get_settings().sql_reader_role


async def test_raw_events_are_stored_redacted(session) -> None:
    from src.services import raw_service

    await _project_with_namespace(session)
    secret = "password=hunter2-" + "x" * 8
    logged = await raw_service.log_raw_event(session, P, event_type="chat", content=secret)
    raw = await raw_service.get_raw_context(session, P, logged["raw_event_id"])
    assert "hunter2" not in raw["raw_content"]


async def test_global_people_and_teams(session) -> None:
    from src.services import global_service

    await global_service.upsert_person(
        session, person_key="Dana.Tran", display_name="Dana Tran", address_as="anh Dana",
        dm_note="DM in Vietnamese",
        identities=[{"system": "github", "handle": "dana-x"}, {"system": "jira", "handle": "dtran"}],
    )
    await global_service.upsert_team(session, team_key="payments", name="Payments",
                                     members=[{"person_key": "dana.tran", "role": "lead"}])
    person = await global_service.find_person(session, system="github", handle="dana-x")
    assert person["person_key"] == "dana.tran"
    assert person["address_as"] == "anh Dana"
    assert person["teams"] == [{"team_key": "payments", "name": "Payments", "role": "lead"}]
    with pytest.raises(ValueError, match="another person"):
        await global_service.upsert_person(
            session, person_key="someone.else", display_name="Someone",
            identities=[{"system": "github", "handle": "dana-x"}],
        )
