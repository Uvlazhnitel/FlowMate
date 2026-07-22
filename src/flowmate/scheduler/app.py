import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from flowmate.ai.errors import AIConfigurationError
from flowmate.ai.factory import create_ai_provider
from flowmate.ai.provider import AIProvider, MeetingReviewProvider
from flowmate.ai.service import DraftParsingService
from flowmate.core.config import Settings, get_settings
from flowmate.core.logging import configure_logging
from flowmate.db.session import create_engine, create_session_factory
from flowmate.reminders.notifications import TelegramNotificationService
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.reminders.processor import ReminderProcessor
from flowmate.stabilization.recovery import AIRecoveryProcessor, MaintenanceProcessor


def create_scheduler(
    processor: ReminderProcessor,
    *,
    interval_seconds: int,
    recovery_processor: AIRecoveryProcessor | None = None,
    recovery_interval_seconds: int = 60,
    maintenance_processor: MaintenanceProcessor | None = None,
    maintenance_interval_seconds: int = 3600,
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
    if recovery_processor is not None:
        scheduler.add_job(
            recovery_processor.process_due_jobs,
            trigger="interval",
            seconds=recovery_interval_seconds,
            id="recover_ai_jobs",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
    if maintenance_processor is not None:
        scheduler.add_job(
            maintenance_processor.run_cleanup,
            trigger="interval",
            seconds=maintenance_interval_seconds,
            id="database_cleanup",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
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
    ai_provider: AIProvider | None = None
    try:
        try:
            ai_provider = create_ai_provider(app_settings)
        except AIConfigurationError:
            logger.warning("ai_recovery_disabled category=incomplete_configuration")
        parsing_service = (
            DraftParsingService(
                ai_provider,
                timezone=ZoneInfo(app_settings.app_timezone),
                active_workspace=app_settings.app_active_workspace,
                timeout_seconds=app_settings.ai_timeout_seconds,
                high_confidence_threshold=app_settings.ai_high_confidence_threshold,
                clarification_confidence_threshold=(
                    app_settings.ai_clarification_confidence_threshold
                ),
            )
            if ai_provider is not None
            else None
        )
        recovery_processor = AIRecoveryProcessor(
            session_factory,
            parsing_service,
            ai_provider if isinstance(ai_provider, MeetingReviewProvider) else None,
            draft_ttl_hours=app_settings.draft_ttl_hours,
            high_threshold=app_settings.ai_high_confidence_threshold,
            clarification_threshold=app_settings.ai_clarification_confidence_threshold,
            max_attempts=app_settings.ai_recovery_max_attempts,
            lease_seconds=app_settings.ai_recovery_lease_seconds,
        )
        maintenance_processor = MaintenanceProcessor(
            session_factory,
            terminal_transcript_days=app_settings.terminal_transcript_retention_days,
            unresolved_transcript_days=(
                app_settings.unresolved_transcript_retention_days
            ),
            expired_record_days=app_settings.expired_record_retention_days,
        )
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
                recovery_processor=recovery_processor,
                recovery_interval_seconds=app_settings.ai_recovery_interval_seconds,
                maintenance_processor=maintenance_processor,
                maintenance_interval_seconds=app_settings.maintenance_interval_seconds,
            )
            scheduler.start()
            logger.info("reminder_scheduler_started")
            await worker_stop.wait()
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=True)
        if ai_provider is not None:
            await ai_provider.close()
        await engine.dispose()
        logger.info("reminder_scheduler_stopped")
