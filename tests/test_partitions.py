from datetime import datetime, timezone

from src.services.partition_service import expired, months_ago, partition_ddl, partition_name_for


def test_partition_name() -> None:
    assert partition_name_for(datetime(2026, 1, 15, tzinfo=timezone.utc)) == "l4_raw_events_2026_01"


def test_ddl_bounds() -> None:
    ddl = partition_ddl(datetime(2026, 12, 3, tzinfo=timezone.utc))
    assert "l4_raw_events_2026_12" in ddl
    assert "2027-01-01" in ddl


def test_months_ago_wraps_year() -> None:
    assert months_ago(datetime(2026, 2, 10, tzinfo=timezone.utc), 6) == datetime(
        2025, 8, 1, tzinfo=timezone.utc
    )


def test_expired_keeps_recent_touches() -> None:
    names = ["l4_raw_events_2026_03", "l4_raw_events_2026_04", "l4_raw_events_2026_10", "other"]
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    assert expired(names, now, 6) == ["l4_raw_events_2026_03"]
