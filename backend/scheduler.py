import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from database import SessionLocal
from models import SyncConfig
from services import sync_service
from services.transaction_sync import run_capi_backfill, run_transaction_sync

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



def _scheduled_daily_run():
    """
    Combined daily job: GHL→Meta audience sync + Stripe→CAPI conversion sync.
    Sends one email with results from both.
    """
    from services import email_service

    db = SessionLocal()
    sync_run = None
    conversion_stats = None

    try:
        config = db.query(SyncConfig).order_by(SyncConfig.id.desc()).first()
        if not config:
            logger.warning("Daily run skipped: no sync configuration found")
            return
        if not config.sync_enabled:
            logger.info("Daily run skipped: sync is disabled")
            return
        if sync_service.is_sync_running():
            logger.warning("Daily run skipped: a sync is already running")
            return

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _run():
                nonlocal sync_run, conversion_stats

                # Part 1: GHL → Meta audience sync (email suppressed)
                logger.info("Daily run: starting GHL-Meta sync...")
                await sync_service.run_sync(config.id, db, skip_email=True)
                sync_run = db.query(SyncConfig).get(config.id)  # refresh
                from models import SyncRun
                sync_run = (
                    db.query(SyncRun)
                    .filter_by(config_id=config.id)
                    .order_by(SyncRun.id.desc())
                    .first()
                )

                # Part 2: Stripe → CAPI conversion sync
                if settings.STRIPE_SECRET_KEY:
                    logger.info("Daily run: starting conversion sync...")
                    result = await run_transaction_sync(db, days_back=7)
                    new_txns = result.get("new", 0)
                    if new_txns > 0:
                        logger.info(f"Daily run: {new_txns} new transactions, sending to CAPI...")
                        capi_result = await run_capi_backfill(db, days_back=7, retry_failed=False)
                        result.update(capi_result)
                    conversion_stats = result
                else:
                    conversion_stats = {"status": "skipped"}

            loop.run_until_complete(_run())
        finally:
            loop.close()

        # Send one combined email
        if sync_run:
            try:
                email_service.send_combined_sync_email(sync_run, conversion_stats)
            except Exception as e:
                logger.error(f"Failed to send combined sync email: {e}")

    except Exception as e:
        logger.error(f"Daily run failed: {e}", exc_info=True)
        if sync_run:
            try:
                from services import email_service
                email_service.send_failure_email(sync_run, str(e))
            except Exception:
                pass
    finally:
        db.close()


def start_scheduler():
    global _scheduler
    cron_kwargs = _parse_cron(settings.SYNC_SCHEDULE_CRON)
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _scheduled_daily_run,
        trigger=CronTrigger(**cron_kwargs),
        id="daily_run",
        name="Daily GHL-Meta + Conversion Sync",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(f"Scheduler started with cron: {settings.SYNC_SCHEDULE_CRON}")


def shutdown_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
