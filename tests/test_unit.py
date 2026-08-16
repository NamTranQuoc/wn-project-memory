from datetime import datetime, timezone

import pytest

from src.services.hashing import (
    build_item_key,
    canonicalize_content,
    compute_content_hash,
    compute_source_hash,
)
from src.services.partition_service import (
    month_start,
    months_ago,
    next_month,
    partition_name_for,
)
from src.services.sanitize import TRUNCATE_SUFFIX, sanitize_and_truncate
from src.services.source_unit_service import _clamp_limit
from src.services.sql_service import ensure_limit, validate_select_only


class TestSanitize:
    def test_short_text_unchanged(self) -> None:
        assert sanitize_and_truncate("hello") == "hello"

    def test_none_becomes_empty(self) -> None:
        assert sanitize_and_truncate(None) == ""

    def test_truncates_long_text(self) -> None:
        text = "a" * 2000
        result = sanitize_and_truncate(text, max_len=1500)
        assert result.endswith(TRUNCATE_SUFFIX)
        assert len(result) == 1500
        assert "use get_raw_context" in result


class TestHashing:
    def test_content_hash_stable(self) -> None:
        assert compute_content_hash("abc") == compute_content_hash("abc")

    def test_content_hash_changes(self) -> None:
        assert compute_content_hash("abc") != compute_content_hash("abd")

    def test_source_hash_sha256_hex(self) -> None:
        h = compute_source_hash("payload")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_canonicalize_normalizes_newlines_and_trailing_space(self) -> None:
        raw = "hello  \r\nworld\r\n"
        assert canonicalize_content(raw) == "hello\nworld"

    def test_item_key_prefers_external_id(self) -> None:
        assert build_item_key(external_id=" msg-1 ", content_hash="abc") == "ext:msg-1"

    def test_item_key_falls_back_to_content_hash(self) -> None:
        assert build_item_key(external_id=None, content_hash="deadbeef") == "hash:deadbeef"
        assert build_item_key(external_id="  ", content_hash="deadbeef") == "hash:deadbeef"

    def test_item_key_collapses_internal_whitespace(self) -> None:
        assert build_item_key(external_id="a   b", content_hash="x") == "ext:a b"


class TestSourceUnitClamp:
    def test_clamp_default_and_cap(self) -> None:
        assert _clamp_limit(None) == 5
        assert _clamp_limit(100) == 5
        assert _clamp_limit(0) == 1
        assert _clamp_limit(3) == 3


class TestSqlGuard:
    def test_rejects_non_select(self) -> None:
        with pytest.raises(ValueError, match="Only SELECT"):
            validate_select_only("DELETE FROM l4_raw_events")

    def test_rejects_multi_statement(self) -> None:
        with pytest.raises(ValueError, match="Multiple statements"):
            validate_select_only("SELECT 1; SELECT 2")

    def test_rejects_ddl_keywords(self) -> None:
        with pytest.raises(ValueError, match="DDL/DML"):
            validate_select_only("SELECT * FROM l4_raw_events WHERE drop = 1")

    def test_injects_limit(self) -> None:
        assert ensure_limit("SELECT * FROM l4_raw_events", limit=10).endswith("LIMIT 10")

    def test_caps_existing_limit(self) -> None:
        assert ensure_limit("SELECT * FROM t LIMIT 100", limit=10) == "SELECT * FROM t LIMIT 10"

    def test_preserves_small_limit(self) -> None:
        assert ensure_limit("SELECT * FROM t LIMIT 3", limit=10) == "SELECT * FROM t LIMIT 3"


class TestPartitionDateMath:
    def test_month_start(self) -> None:
        dt = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
        assert month_start(dt) == datetime(2026, 8, 1, tzinfo=timezone.utc)

    def test_next_month_rollover(self) -> None:
        dt = datetime(2026, 12, 1, tzinfo=timezone.utc)
        assert next_month(dt) == datetime(2027, 1, 1, tzinfo=timezone.utc)

    def test_partition_name(self) -> None:
        assert partition_name_for(datetime(2026, 8, 20, tzinfo=timezone.utc)) == (
            "l4_raw_events_2026_08"
        )

    def test_months_ago(self) -> None:
        dt = datetime(2026, 8, 1, tzinfo=timezone.utc)
        assert months_ago(dt, 6) == datetime(2026, 2, 1, tzinfo=timezone.utc)
        assert months_ago(dt, 8) == datetime(2025, 12, 1, tzinfo=timezone.utc)


class TestReindexProtection:
    def test_protected_keys_and_types(self) -> None:
        from types import SimpleNamespace

        from src.models import SourceType
        from src.services.source_service import (
            LEGACY_UNATTRIBUTED_KEY,
            USER_SESSION_KEY,
            is_protected_reindex_source,
        )

        assert is_protected_reindex_source(
            SimpleNamespace(source_key=USER_SESSION_KEY, source_type=SourceType.other)
        )
        assert is_protected_reindex_source(
            SimpleNamespace(
                source_key=LEGACY_UNATTRIBUTED_KEY, source_type=SourceType.other
            )
        )
        assert is_protected_reindex_source(
            SimpleNamespace(source_key="local_plans", source_type=SourceType.local_file)
        )
        assert not is_protected_reindex_source(
            SimpleNamespace(source_key="teams_war_room", source_type=SourceType.teams_chat)
        )


@pytest.mark.asyncio
async def test_fact_key_required() -> None:
    from unittest.mock import MagicMock

    from src.services import fact_service

    session = MagicMock()
    with pytest.raises(ValueError, match="fact_key is required"):
        await fact_service.upsert_fact(
            session,
            "/tmp/x",
            fact_key="  ",
            kind="fact",
            title="t",
            content="c",
        )
