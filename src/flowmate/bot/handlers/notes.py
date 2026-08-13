# ruff: noqa: RUF001
import logging
import re
from dataclasses import dataclass
from typing import Literal
from zoneinfo import ZoneInfo

from aiogram.types import Message, Update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.errors import AIError, safe_ai_error_code
from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftSource,
    ManagementIntent,
    SearchIntent,
)
from flowmate.ai.service import DraftParsingService
from flowmate.bot.handlers.drafts import (
    DRAFT_ANALYZING_MESSAGE,
    DRAFT_FAILED_MESSAGE,
    DRAFT_RETRY_MESSAGE,
    analyze_note_content,
)
from flowmate.bot.handlers.navigation.search import execute_search_intent
from flowmate.bot.handlers.work_items.management import (
    ManagementIntentOutcome,
    execute_management_intent,
)
from flowmate.bot.presentation import (
    TelegramDisplayContext,
    format_datetime,
    html_text,
    preview,
)
from flowmate.db.drafts import create_parsing_draft, get_draft_by_source_note
from flowmate.db.models import (
    DraftSession,
    Note,
    User,
    WorkItemActionSession,
)
from flowmate.db.notes import (
    NoteSource,
    create_note_idempotently,
    get_note_by_telegram_update_id,
    list_recent_notes_for_user,
)
from flowmate.db.users import get_or_create_telegram_user, get_user_by_telegram_id
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.sync import ReminderPolicy
from flowmate.task_engine.action_sessions import finish_action_session
from flowmate.task_engine.conversion import DraftConversionService
from flowmate.task_engine.intents import management_update_was_processed
from flowmate.task_engine.rescheduling import ReschedulingService
from flowmate.workspaces import activate_workspace, active_workspace

NOTE_SAVED_MESSAGE = "✅ Заметка сохранена"
NOTE_ALREADY_SAVED_MESSAGE = "Эта запись уже сохранена."
MANAGEMENT_ALREADY_PROCESSED_MESSAGE = "Изменение уже обработано."
NOTE_EMPTY_MESSAGE = "Заметка не может быть пустой."
NOTE_SAVE_FAILED_MESSAGE = "Не удалось сохранить заметку. Попробуйте позже."
NOTE_LIST_FAILED_MESSAGE = "Не удалось загрузить заметки. Попробуйте позже."
NO_NOTES_MESSAGE = "🗒 Заметок пока нет"
NOTE_PREVIEW_LENGTH = 300
NOTE_LIST_LIMIT = 10

NoteSaveStatus = Literal["created", "duplicate", "failed"]


@dataclass(frozen=True, slots=True)
class NoteSaveOutcome:
    status: NoteSaveStatus
    note: Note | None = None
    user: User | None = None
    draft: DraftSession | None = None


logger = logging.getLogger(__name__)

CREATION_MARKERS = (
    "добавить",
    "добавь",
    "создать",
    "создай",
    "записать",
    "запиши",
    "напомнить",
    "напомни",
    "нужно сделать",
)
MANAGEMENT_MARKERS = (
    "заверши",
    "выполни задачу",
    "отмени задачу",
    "перенеси задачу",
    "перенеси существующ",
    "верни задачу",
    "переоткрой",
    "переимен",
    "измени название",
    "измени описание",
    "удали описание",
    "change title",
    "change description",
)
WORKSPACE_PREFIX = re.compile(
    r"^\s*(?P<label>работа|в\s+работу|личное|в\s+личное)\s*:\s*",
    re.IGNORECASE,
)


def explicitly_manages_existing_item(message: Message, content: str) -> bool:
    if message.reply_to_message is not None:
        return True
    normalized = content.casefold()
    if any(marker in normalized for marker in CREATION_MARKERS):
        return False
    return any(marker in normalized for marker in MANAGEMENT_MARKERS)


def capture_workspace_override(value: str) -> tuple[str, str | None]:
    match = WORKSPACE_PREFIX.match(value)
    if match is None:
        return value, None
    label = " ".join(match.group("label").casefold().split())
    workspace = "work" if "работ" in label else "personal"
    return value[match.end() :].strip(), workspace


def selected_capture_workspace(
    analysis: DraftAnalysisResult | None,
    *,
    current: str,
    explicit: str | None,
    high_confidence_threshold: float,
) -> tuple[str, bool]:
    if explicit is not None:
        return explicit, True
    if (
        analysis is not None
        and analysis.workspace_candidate is not None
        and analysis.workspace_confidence >= high_confidence_threshold
    ):
        return analysis.workspace_candidate, True
    return current, False


async def save_note_for_message(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    *,
    content: str,
    source: NoteSource,
    create_draft: bool = False,
    draft_ttl_hours: int = 24,
    default_workspace: str = "personal",
    capture_session: WorkItemActionSession | None = None,
    workspace_override: str | None = None,
    update_active_workspace: bool = False,
) -> NoteSaveOutcome:
    telegram_user = message.from_user
    if telegram_user is None:
        return NoteSaveOutcome("failed")

    try:
        user, _ = await get_or_create_telegram_user(
            db_session,
            telegram_user.id,
            display_name=telegram_user.full_name[:255],
            active_workspace=default_workspace,
        )
        if workspace_override is not None:
            activate_workspace(
                db_session,
                user_id=user.id,
                workspace=workspace_override,
            )
            if update_active_workspace:
                user.active_workspace = workspace_override
        note, created = await create_note_idempotently(
            db_session,
            user_id=user.id,
            content=content,
            source=source,
            telegram_update_id=event_update.update_id,
        )
        draft = (
            await get_draft_by_source_note(db_session, note.id)
            if create_draft
            else None
        )
        if create_draft and created:
            draft = await create_parsing_draft(
                db_session,
                user_id=user.id,
                source_note_id=note.id,
                ttl_hours=draft_ttl_hours,
            )
        if created and capture_session is not None:
            await finish_action_session(
                db_session,
                capture_session,
                status="completed",
            )
        await db_session.commit()
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_note_database_failed user_id=%s operation=create",
            telegram_user.id,
        )
        return NoteSaveOutcome("failed")

    return NoteSaveOutcome(
        "created" if created else "duplicate",
        note=note,
        user=user,
        draft=draft,
    )


async def text_note(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    draft_parsing_service: DraftParsingService | None = None,
    draft_ttl_hours: int = 24,
    ai_high_confidence_threshold: float = 0.8,
    work_item_action_ttl_minutes: int = 30,
    app_timezone: ZoneInfo | None = None,
    reminder_policy: ReminderPolicy | None = None,
    default_workspace: str = "personal",
    active_capture: WorkItemActionSession | None = None,
    draft_conversion_service: DraftConversionService | None = None,
    notification_defaults: NotificationDefaults | None = None,
    rescheduling_service: ReschedulingService | None = None,
) -> None:
    content = message.text.strip() if message.text is not None else ""
    if not content:
        await message.answer(NOTE_EMPTY_MESSAGE)
        return

    try:
        existing_note = await get_note_by_telegram_update_id(
            db_session,
            event_update.update_id,
        )
    except SQLAlchemyError:
        await db_session.rollback()
        await message.answer(NOTE_SAVE_FAILED_MESSAGE)
        return
    if isinstance(existing_note, Note):
        await db_session.rollback()
        await message.answer(NOTE_ALREADY_SAVED_MESSAGE)
        return
    telegram_user = message.from_user
    if telegram_user is not None:
        try:
            management_processed = await management_update_was_processed(
                db_session,
                telegram_user.id,
                event_update.update_id,
            )
        except SQLAlchemyError:
            await db_session.rollback()
            await message.answer(NOTE_SAVE_FAILED_MESSAGE)
            return
        if management_processed:
            await db_session.rollback()
            await message.answer(MANAGEMENT_ALREADY_PROCESSED_MESSAGE)
            return
    await db_session.rollback()

    parse_content, explicit_workspace = capture_workspace_override(content)
    routed: DraftAnalysisResult | ManagementIntent | SearchIntent | None = None
    if draft_parsing_service is not None:
        try:
            workspace = active_workspace(db_session)
            routed = (
                await draft_parsing_service.parse(
                    parse_content,
                    source=DraftSource.TEXT,
                    active_workspace=workspace,
                )
                if active_capture is not None
                else await draft_parsing_service.parse_text(
                    parse_content,
                    active_workspace=workspace,
                )
                if workspace is not None
                else await draft_parsing_service.parse_text(parse_content)
            )
        except AIError as error:
            logger.warning(
                "telegram_text_routing_failed user_id=%s category=%s",
                message.from_user.id if message.from_user else 0,
                safe_ai_error_code(error),
            )
    if isinstance(routed, ManagementIntent):
        timezone = app_timezone or ZoneInfo("UTC")
        try:
            management_outcome = await execute_management_intent(
                message,
                event_update,
                db_session,
                routed,
                high_confidence_threshold=ai_high_confidence_threshold,
                action_ttl_minutes=work_item_action_ttl_minutes,
                app_timezone=timezone,
                reminder_policy=reminder_policy,
                notification_defaults=notification_defaults,
                rescheduling_service=rescheduling_service,
            )
        except SQLAlchemyError:
            await db_session.rollback()
            logger.error(
                "telegram_management_database_failed user_id=%s",
                message.from_user.id if message.from_user else 0,
            )
            await message.answer("Не удалось изменить запись. Попробуйте позже.")
            return
        if management_outcome is not ManagementIntentOutcome.NOT_FOUND:
            return
        if explicitly_manages_existing_item(message, content):
            await message.answer("Подходящая запись не найдена.")
            return
        if draft_parsing_service is not None:
            try:
                routed = await draft_parsing_service.parse(
                    parse_content,
                    source=DraftSource.TEXT,
                    active_workspace=active_workspace(db_session),
                )
            except AIError:
                routed = None
    if isinstance(routed, SearchIntent):
        timezone = app_timezone or ZoneInfo("UTC")
        try:
            await execute_search_intent(
                message,
                event_update,
                db_session,
                routed,
                high_confidence_threshold=ai_high_confidence_threshold,
                action_ttl_minutes=work_item_action_ttl_minutes,
                timezone=timezone,
            )
        except SQLAlchemyError:
            await db_session.rollback()
            logger.error(
                "telegram_search_database_failed user_id=%s",
                message.from_user.id if message.from_user else 0,
            )
            await message.answer("Не удалось выполнить поиск. Попробуйте позже.")
        return

    current_workspace = active_workspace(db_session) or default_workspace
    selected_workspace, update_workspace = selected_capture_workspace(
        routed if isinstance(routed, DraftAnalysisResult) else None,
        current=current_workspace,
        explicit=explicit_workspace,
        high_confidence_threshold=ai_high_confidence_threshold,
    )
    create_draft = draft_parsing_service is not None and (
        active_capture is not None or isinstance(routed, DraftAnalysisResult)
    )
    result = await save_note_for_message(
        message,
        event_update,
        db_session,
        content=content,
        source="text",
        create_draft=create_draft,
        draft_ttl_hours=draft_ttl_hours,
        default_workspace=default_workspace,
        capture_session=active_capture,
        workspace_override=selected_workspace,
        update_active_workspace=update_workspace,
    )
    if result.status == "failed":
        await message.answer(NOTE_SAVE_FAILED_MESSAGE)
    elif result.status == "duplicate":
        await message.answer(NOTE_ALREADY_SAVED_MESSAGE)
    elif (
        draft_parsing_service is not None
        and create_draft
        and message.from_user is not None
        and result.draft is not None
    ):
        await message.answer(DRAFT_ANALYZING_MESSAGE)
        await analyze_note_content(
            message,
            content=parse_content,
            telegram_user_id=message.from_user.id,
            source=DraftSource.TEXT,
            service=draft_parsing_service,
            db_session=db_session,
            draft=result.draft,
            draft_ttl_hours=draft_ttl_hours,
            precomputed_result=(
                routed if isinstance(routed, DraftAnalysisResult) else None
            ),
            active_workspace=result.draft.workspace,
            high_confidence_threshold=ai_high_confidence_threshold,
            draft_conversion_service=draft_conversion_service,
            notification_defaults=notification_defaults,
            failure_message=(
                DRAFT_RETRY_MESSAGE
                if active_capture is not None
                else DRAFT_FAILED_MESSAGE
            ),
        )
    elif draft_parsing_service is not None and routed is None:
        await message.answer(NOTE_SAVED_MESSAGE)
        await message.answer(DRAFT_FAILED_MESSAGE)
    else:
        await message.answer(NOTE_SAVED_MESSAGE)


def format_note_preview(
    note: Note,
    position: int,
    display: TelegramDisplayContext | None = None,
) -> str:
    context = display or TelegramDisplayContext(ZoneInfo("UTC"))
    normalized = preview(
        note.content or "Исходная расшифровка очищена",
        NOTE_PREVIEW_LENGTH,
    )
    source_icon = "🎙" if note.source == "voice" else "🗒"
    created_at = format_datetime(note.created_at, context)
    return (
        f"{position}. {source_icon} <b>{html_text(created_at)}</b>\n"
        f"{html_text(normalized)}"
    )


async def notes_command(
    message: Message,
    db_session: AsyncSession,
    notification_defaults: NotificationDefaults | None = None,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return

    try:
        user = await get_user_by_telegram_id(db_session, telegram_user.id)
        if user is None:
            notes = []
            preferences = None
        else:
            notes = await list_recent_notes_for_user(
                db_session,
                user.id,
                limit=NOTE_LIST_LIMIT,
            )
            preferences = (
                await get_effective_notification_preferences(
                    db_session, user.id, notification_defaults
                )
                if notification_defaults is not None
                else None
            )
        await db_session.rollback()
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_note_database_failed user_id=%s operation=list",
            telegram_user.id,
        )
        await message.answer(NOTE_LIST_FAILED_MESSAGE)
        return

    if not notes:
        await message.answer(NO_NOTES_MESSAGE)
        return

    display = (
        TelegramDisplayContext.from_preferences(preferences)
        if preferences is not None
        else TelegramDisplayContext(ZoneInfo("UTC"))
    )
    response = "🗒 <b>Последние заметки</b>\n\n" + "\n\n".join(
        format_note_preview(note, position, display)
        for position, note in enumerate(notes, start=1)
    )
    await message.answer(response, parse_mode="HTML")
