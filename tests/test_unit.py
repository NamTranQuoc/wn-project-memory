from datetime import datetime, timezone

import pytest

from src.services.hashing import compute_content_hash, compute_source_hash
from src.services.partition_service import (
    month_start,
    months_ago,
    next_month,
    partition_name_for,
)
from src.services.sanitize import TRUNCATE_SUFFIX, sanitize_and_truncate
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
