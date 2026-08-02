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


def partition_name_for(dt: datetime) -> str:
    start = month_start(dt)
    return f"l4_raw_events_{start.year}_{start.month:02d}"


def months_ago(dt: datetime, months: int) -> datetime:
    year = dt.year
    month = dt.month - months
    while month <= 0:
        month += 12
        year -= 1
    return datetime(year, month, 1, tzinfo=timezone.utc)


async def ensure_partition_for(session: AsyncSession, dt: datetime) -> str:
    name = partition_name_for(dt)
    # DDL cannot take bind params (asyncpg/Postgres). Name is generated locally —
    # still validate before interpolating into SQL.
    if not PARTITION_NAME_RE.match(name):
        raise ValueError(f"Invalid partition name: {name}")

    start = month_start(dt)
    end = next_month(start)
    start_lit = start.isoformat()
    end_lit = end.isoformat()
    await session.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {name}
            PARTITION OF l4_raw_events
            FOR VALUES FROM ('{start_lit}') TO ('{end_lit}')
            """
        )
    )
    logger.info("Ensured partition %s [%s, %s)", name, start_lit, end_lit)
    return name


async def ensure_next_month_partition(session: AsyncSession) -> list[str]:
    """Create partitions for the current month and next month if missing."""
    now = datetime.now(timezone.utc)
    created = []
    for dt in (now, next_month(now)):
        created.append(await ensure_partition_for(session, dt))
    return created


async def list_l4_partitions(session: AsyncSession) -> list[str]:
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


async def drop_old_partitions(session: AsyncSession, months: int | None = None) -> list[str]:
    """Drop L4 partitions older than retention window (default 6 months)."""
    retention = months if months is not None else get_settings().l4_retention_months
    cutoff = months_ago(datetime.now(timezone.utc), retention)
    dropped: list[str] = []

    for name in await list_l4_partitions(session):
        match = PARTITION_NAME_RE.match(name)
        if not match:
            continue
        year, month = int(match.group(1)), int(match.group(2))
        part_start = datetime(year, month, 1, tzinfo=timezone.utc)
        if part_start < cutoff:
            await session.execute(text(f"DROP TABLE IF EXISTS {name}"))
            dropped.append(name)
            logger.info("Dropped expired partition %s (older than %s)", name, cutoff.date())

    return dropped
