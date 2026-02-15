import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from database import SessionLocal
from models import SyncConfig
from services import sync_service

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _parse_cron(cron_str: str) -> dict:
    """Parse a cron string '0 2 * * *' into CronTrigger kwargs."""
    parts = cron_str.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {cron_str}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def _scheduled_sync():
    """Called by the scheduler. Runs the sync in an async context."""
    db = SessionLocal()
    try:
        config = db.query(SyncConfig).order_by(SyncConfig.id.desc()).first()
        if not config:
            logger.warning("Scheduled sync skipped: no configuration found")
            return
        if not config.sync_enabled:
            logger.info("Scheduled sync skipped: sync is disabled")
            return
        if sync_service.is_sync_running():
            logger.warning("Scheduled sync skipped: a sync is already running")
            return

        logger.info("Scheduled sync starting...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(sync_service.run_sync(config.id, db))
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"Scheduled sync failed: {e}", exc_info=True)
    finally:
        db.close()


def start_scheduler():
    global _scheduler
    cron_kwargs = _parse_cron(settings.SYNC_SCHEDULE_CRON)
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _scheduled_sync,
        trigger=CronTrigger(**cron_kwargs),
        id="daily_sync",
        name="Daily GHL-Meta Sync",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(f"Scheduler started with cron: {settings.SYNC_SCHEDULE_CRON}")


def shutdown_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
