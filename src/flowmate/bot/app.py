import logging
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowmate.ai.errors import AIConfigurationError
from flowmate.ai.factory import create_ai_provider
from flowmate.ai.provider import AIProvider, SnoozeTimeProvider
from flowmate.ai.service import DraftParsingService
from flowmate.bot.handlers import create_router
from flowmate.core.config import Settings, get_settings
from flowmate.core.logging import configure_logging
from flowmate.db.session import create_engine, create_session_factory
from flowmate.reminders.parsing import SnoozeParsingService
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.reminders.sync import ReminderPolicy
from flowmate.speech.errors import SpeechConfigurationError
from flowmate.speech.factory import create_speech_provider
from flowmate.speech.provider import SpeechToTextProvider
from flowmate.speech.service import TranscriptionService
from flowmate.speech.temp_files import TemporaryAudioFileService
from flowmate.task_engine.conversion import DraftConversionService


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    transcription_service: TranscriptionService | None = None,
    draft_parsing_service: DraftParsingService | None = None,
) -> Dispatcher:
    reminder_policy = ReminderPolicy(
        deadline_lead_minutes=settings.deadline_reminder_lead_minutes
    )
    notification_defaults = NotificationDefaults.from_settings(settings)
    dispatcher = Dispatcher(
        transcription_service=transcription_service,
        draft_parsing_service=draft_parsing_service,
        draft_conversion_service=DraftConversionService(
            reminder_policy=reminder_policy
        ),
        reminder_policy=reminder_policy,
        draft_ttl_hours=settings.draft_ttl_hours,
        app_timezone=ZoneInfo(settings.app_timezone),
        ai_high_confidence_threshold=settings.ai_high_confidence_threshold,
        work_item_action_ttl_minutes=settings.work_item_action_ttl_minutes,
        notification_defaults=notification_defaults,
        snooze_parsing_service=SnoozeParsingService(
            None,
            timeout_seconds=settings.ai_timeout_seconds,
        ),
    )
    dispatcher.include_router(
        create_router(settings.telegram_allowed_user_ids, session_factory, engine)
    )
    return dispatcher


async def run_bot(settings: Settings | None = None) -> None:
    app_settings = (settings or get_settings()).require_bot()
    configure_logging(
        app_settings.log_level,
        structured=app_settings.app_env == "production",
    )
    bot_token_secret = app_settings.telegram_bot_token
    if bot_token_secret is None:  # Kept explicit for static type narrowing.
        raise RuntimeError("Telegram bot token validation failed")
    bot_token = bot_token_secret.get_secret_value()

    engine = create_engine(app_settings.database_url)
    session_factory = create_session_factory(engine)
    logger = logging.getLogger(__name__)
    speech_provider: SpeechToTextProvider | None = None
    ai_provider: AIProvider | None = None
    try:
        try:
            speech_provider = create_speech_provider(app_settings)
        except SpeechConfigurationError:
            logger.warning("speech_provider_disabled category=incomplete_configuration")

        transcription_service = (
            TranscriptionService(
                speech_provider,
                TemporaryAudioFileService(),
                timeout_seconds=app_settings.speech_timeout_seconds,
                max_file_size_bytes=app_settings.speech_max_file_size_bytes,
            )
            if speech_provider is not None
            else None
        )
        try:
            ai_provider = create_ai_provider(app_settings)
        except AIConfigurationError:
            logger.warning("ai_provider_disabled category=incomplete_configuration")

        draft_parsing_service = (
            DraftParsingService(
                ai_provider,
                timezone=ZoneInfo(app_settings.app_timezone),
                active_workspace=app_settings.app_active_workspace,
                timeout_seconds=app_settings.ai_timeout_seconds,
                high_confidence_threshold=(app_settings.ai_high_confidence_threshold),
                clarification_confidence_threshold=(
                    app_settings.ai_clarification_confidence_threshold
                ),
            )
            if ai_provider is not None
            else None
        )
        dispatcher = create_dispatcher(
            app_settings,
            session_factory,
            engine,
            transcription_service,
            draft_parsing_service,
        )
        dispatcher["snooze_parsing_service"] = SnoozeParsingService(
            ai_provider if isinstance(ai_provider, SnoozeTimeProvider) else None,
            timeout_seconds=app_settings.ai_timeout_seconds,
        )
        async with Bot(token=bot_token) as bot:
            logger.info("telegram_bot_started")
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
                close_bot_session=False,
            )
    finally:
        if ai_provider is not None:
            await ai_provider.close()
        if speech_provider is not None:
            await speech_provider.close()
        await engine.dispose()
        logger.info("telegram_bot_stopped")
