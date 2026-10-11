"""L4 monthly partitions by last_touched_at: create ahead, drop after the idle window."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings

logger = logging.getLogger(__name__)

PARTITION_NAME_RE = re.compile(r"^l4_raw_events_(\d{4})_(\d{2})$")


def month_start(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)


def next_month(dt: datetime) -> datetime:
    if dt.month == 12:
        return datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)


def months_ago(dt: datetime, months: int) -> datetime:
    year, month = dt.year, dt.month - months
    while month <= 0:
        month += 12
        year -= 1
    return datetime(year, month, 1, tzinfo=timezone.utc)


def partition_name_for(dt: datetime) -> str:
    start = month_start(dt)
    return f"l4_raw_events_{start.year}_{start.month:02d}"


def partition_ddl(dt: datetime) -> str:
    name = partition_name_for(dt)
    # DDL cannot take bind params. The name is generated here; validate anyway.
    if not PARTITION_NAME_RE.match(name):
        raise ValueError(f"Invalid partition name: {name}")
    start = month_start(dt)
    end = next_month(start)
    return (
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF l4_raw_events "
        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
    )


async def ensure_partitions(session: AsyncSession) -> list[str]:
    """Partitions for this month and next month."""
    now = datetime.now(timezone.utc)
    names = []
    for dt in (now, next_month(now)):
        await session.execute(text(partition_ddl(dt)))
        names.append(partition_name_for(dt))
    return names


async def list_partitions(session: AsyncSession) -> list[str]:
    result = await session.execute(
        text(
            """
            SELECT c.relname
            FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = 'l4_raw_events'
            ORDER BY c.relname
            """
        )
    )
    return [row[0] for row in result.all()]


def expired(names: list[str], now: datetime, months: int) -> list[str]:
    cutoff = months_ago(now, months)
    out = []
    for name in names:
        match = PARTITION_NAME_RE.match(name)
        if match and datetime(int(match.group(1)), int(match.group(2)), 1, tzinfo=timezone.utc) < cutoff:
            out.append(name)
    return out


async def drop_idle_partitions(session: AsyncSession, months: int | None = None) -> list[str]:
    """Drop partitions whose month of last touch is older than the idle window.

    A raw event that was read or cited moved to a newer partition, so it survives.
    """
    window = months if months is not None else get_settings().l4_idle_retention_months
    dropped = expired(await list_partitions(session), datetime.now(timezone.utc), window)
    for name in dropped:
        await session.execute(text(f"DROP TABLE IF EXISTS {name}"))
        logger.info("Dropped idle L4 partition %s", name)
    return dropped
