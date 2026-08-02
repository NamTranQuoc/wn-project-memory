import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.core.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _run_partition_maintenance() -> None:
    from src.services.partition_service import (
        drop_old_partitions,
        ensure_next_month_partition,
    )

    async with AsyncSessionLocal() as session:
        try:
            await ensure_next_month_partition(session)
            await drop_old_partitions(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Partition maintenance failed")


def start_scheduler() -> None:
    if scheduler.running:
        return

    scheduler.add_job(
        _run_partition_maintenance,
        trigger="cron",
        hour=0,
        minute=5,
        id="partition_maintenance",
        replace_existing=True,
    )
    # Bootstrap partitions immediately on startup
    scheduler.add_job(
        _run_partition_maintenance,
        trigger="date",
        id="partition_bootstrap",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("APScheduler started")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
