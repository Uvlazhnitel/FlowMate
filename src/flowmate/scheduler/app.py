import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from flowmate.core.config import Settings, get_settings
from flowmate.core.logging import configure_logging
from flowmate.db.session import create_engine, create_session_factory
from flowmate.reminders.notifications import TelegramNotificationService
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.reminders.processor import ReminderProcessor


def create_scheduler(
    processor: ReminderProcessor,
    *,
    interval_seconds: int,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        processor.process_due_reminders,
        trigger="interval",
        seconds=interval_seconds,
        id="process_due_reminders",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        next_run_time=datetime.now(UTC),
    )
    return scheduler


async def run_scheduler(
    settings: Settings | None = None,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    app_settings = (settings or get_settings()).require_scheduler()
    configure_logging(
        app_settings.log_level,
        structured=app_settings.app_env == "production",
    )
    token = app_settings.telegram_bot_token
    if token is None:
        raise RuntimeError("Telegram bot token validation failed")
    engine = create_engine(app_settings.database_url)
    session_factory = create_session_factory(engine)
    logger = logging.getLogger(__name__)
    worker_stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    if stop_event is None:
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signal_name, worker_stop.set)
            except NotImplementedError:
                pass
    scheduler: AsyncIOScheduler | None = None
    try:
        async with Bot(token=token.get_secret_value()) as bot:
            processor = ReminderProcessor(
                session_factory,
                TelegramNotificationService(bot),
                batch_size=app_settings.reminder_batch_size,
                max_attempts=app_settings.reminder_max_attempts,
                retry_delay=timedelta(
                    seconds=app_settings.reminder_retry_delay_seconds
                ),
                processing_timeout=timedelta(
                    seconds=app_settings.reminder_processing_timeout_seconds
                ),
                delivery_timeout_seconds=(
                    app_settings.reminder_delivery_timeout_seconds
                ),
                notification_defaults=NotificationDefaults.from_settings(app_settings),
            )
            scheduler = create_scheduler(
                processor,
                interval_seconds=app_settings.scheduler_interval_seconds,
            )
            scheduler.start()
            logger.info("reminder_scheduler_started")
            await worker_stop.wait()
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=True)
        await engine.dispose()
        logger.info("reminder_scheduler_stopped")
