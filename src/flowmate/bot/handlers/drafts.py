# ruff: noqa: RUF001
import logging
from datetime import datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.errors import AIError, safe_ai_error_code
from flowmate.ai.prompt_versions import REFINEMENT_PROMPT_VERSION
from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftItemAssessment,
    DraftItemType,
    DraftReadiness,
    DraftSource,
    TemporalStatus,
)
from flowmate.ai.service import DraftParsingService
from flowmate.bot.callback_data import encode_revision
from flowmate.bot.callback_feedback import CallbackFeedback
from flowmate.bot.menu import answer_with_main_menu, restore_main_menu
from flowmate.bot.presentation import (
    TelegramDisplayContext,
    format_datetime,
    html_text,
    item_presentation,
    join_html_blocks,
    pluralize,
    preview,
)
from flowmate.db.drafts import (
    claim_update,
    clear_processing_update,
    get_draft_for_user,
    load_analysis,
    replace_draft_analysis,
    set_question_message_id,
    transition_draft,
    utc_now,
)
from flowmate.db.models import DraftSession
from flowmate.db.users import get_user_by_telegram_id
from flowmate.drafts.questions import ClarificationQuestion, next_clarification_question
from flowmate.reminders.preferences import (
    EffectiveNotificationPreferences,
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.stabilization.jobs import enqueue_ai_job
from flowmate.task_engine.conversion import (
    DraftConversionError,
    DraftConversionResult,
    DraftConversionService,
)
from flowmate.task_engine.enums import WorkItemType
from flowmate.task_engine.management import work_item_revision
from flowmate.workspaces import WORKSPACE_LABELS

DRAFT_ANALYZING_MESSAGE = "⏳ Запись принята. Разбираю…"
DRAFT_FAILED_MESSAGE = "Не получилось разобрать запись. Она сохранена в Inbox."
DRAFT_RETRY_MESSAGE = (
    "Не получилось разобрать запись сейчас. Она сохранена в Inbox; "
    "повторю автоматически."
)
DRAFT_CANCELLED_MESSAGE = "Черновик отменён. Исходная запись сохранена."
DRAFT_CONVERSION_FAILED_MESSAGE = (
    "Не получилось создать запись. Черновик сохранён — можно повторить позже."
)
DRAFT_EXPIRED_MESSAGE = "Этот черновик уже недоступен. Запишите пункт заново."
DRAFT_NOT_FOUND_MESSAGE = "Сейчас нет активного черновика."
DRAFT_BUSY_MESSAGE = "Предыдущий ответ ещё обрабатывается. Подождите немного."
DRAFT_REPLY_REQUIRED_MESSAGE = "Ответьте через Reply на последнее уточнение."
DRAFT_CHANGE_QUESTION = "Что нужно изменить?"

logger = logging.getLogger(__name__)


def apply_default_reminder_time(
    analysis: DraftAnalysisResult,
    *,
    default_time: time,
) -> DraftAnalysisResult:
    timezone = ZoneInfo(analysis.context.timezone)
    changed = False
    assessments: list[DraftItemAssessment] = []
    for assessment in analysis.items:
        item = assessment.item
        candidate = item.reminder_candidate
        if (
            candidate is None
            or candidate.status is not TemporalStatus.RESOLVED
            or candidate.normalized_value is None
            or candidate.time_was_explicit
        ):
            assessments.append(assessment)
            continue
        local_date = candidate.normalized_value.astimezone(timezone).date()
        resolved = resolve_local_datetime(local_date, default_time, timezone)
        updated_candidate = candidate.model_copy(update={"normalized_value": resolved})
        updated_item = item.model_copy(update={"reminder_candidate": updated_candidate})
        assessments.append(assessment.model_copy(update={"item": updated_item}))
        changed = True
    return analysis.model_copy(update={"items": assessments}) if changed else analysis


def fast_capture_is_ready(
    analysis: DraftAnalysisResult,
    *,
    high_confidence_threshold: float,
) -> bool:
    if analysis.confidence < high_confidence_threshold:
        return False
    for assessment in analysis.items:
        item = assessment.item
        if (
            assessment.readiness is not DraftReadiness.READY
            or item.type is DraftItemType.UNKNOWN
            or item.confidence < high_confidence_threshold
        ):
            return False
        for candidate in (item.due_date_candidate, item.reminder_candidate):
            if (
                candidate is not None
                and candidate.status is not TemporalStatus.RESOLVED
            ):
                return False
    return True


def fast_capture_summary(
    analysis: DraftAnalysisResult,
    *,
    workspace: str,
    display: TelegramDisplayContext | None = None,
) -> str:
    context = display or TelegramDisplayContext(
        timezone=ZoneInfo(analysis.context.timezone)
    )
    count = len(analysis.items)
    heading = (
        "✅ <b>Запись создана</b>"
        if count == 1
        else f"✅ <b>Создано {count} "
        f"{pluralize(count, ('запись', 'записи', 'записей'))}</b>"
    )
    blocks = [heading]
    for position, assessment in enumerate(analysis.items, start=1):
        item = assessment.item
        icon, label = item_presentation(item.type.value)
        prefix = f"{position}. " if count > 1 else ""
        lines = [
            f"{prefix}{icon} <b>{label}</b>",
            html_text(preview(item.title, 240)),
        ]
        candidate = item.reminder_candidate or item.due_date_candidate
        if (
            candidate is not None
            and candidate.status is TemporalStatus.RESOLVED
            and candidate.normalized_value is not None
        ):
            date_only = (
                candidate is item.due_date_candidate and not candidate.time_was_explicit
            )
            label_prefix = (
                "⏰ Напомнить" if candidate is item.reminder_candidate else "📅 Срок"
            )
            lines.append(
                f"{label_prefix}: "
                f"{
                    format_datetime(
                        candidate.normalized_value,
                        context,
                        date_only=date_only,
                    )
                }"
            )
        blocks.append("\n".join(lines))
    blocks.append(f"📂 {WORKSPACE_LABELS[workspace]}")
    return "\n\n".join(blocks)


async def mark_draft_failed_safely(
    db_session: AsyncSession,
    draft: DraftSession,
) -> None:
    try:
        await transition_draft(db_session, draft, "failed")
        await db_session.commit()
    except SQLAlchemyError:
        await db_session.rollback()


async def release_processing_update_safely(
    db_session: AsyncSession,
    draft: DraftSession,
) -> None:
    try:
        await clear_processing_update(db_session, draft)
        await db_session.commit()
    except SQLAlchemyError:
        await db_session.rollback()


def draft_display_context(
    result: DraftAnalysisResult,
    preferences: EffectiveNotificationPreferences | None = None,
) -> TelegramDisplayContext:
    if preferences is not None:
        return TelegramDisplayContext.from_preferences(preferences)
    return TelegramDisplayContext(timezone=ZoneInfo(result.context.timezone))


def format_draft_blocks(
    result: DraftAnalysisResult,
    *,
    display: TelegramDisplayContext | None = None,
) -> list[str]:
    context = display or draft_display_context(result)
    count = len(result.items)
    heading = (
        "📝 <b>Проверьте запись</b>"
        if count == 1
        else f"📝 <b>Проверьте {count} "
        f"{pluralize(count, ('запись', 'записи', 'записей'))}</b>"
    )
    blocks = [heading]
    for position, assessment in enumerate(result.items, start=1):
        item = assessment.item
        icon, label = item_presentation(item.type.value)
        prefix = f"{position}. " if count > 1 else ""
        lines = [
            f"{prefix}{icon} <b>{label}</b>",
            html_text(preview(item.title, 300)),
        ]
        for candidate, date_label, date_icon in (
            (item.due_date_candidate, "Срок", "📅"),
            (item.reminder_candidate, "Напомнить", "⏰"),
        ):
            if (
                candidate is None
                or candidate.status is not TemporalStatus.RESOLVED
                or candidate.normalized_value is None
            ):
                continue
            lines.append(
                f"{date_icon} {date_label}: "
                f"{
                    format_datetime(
                        candidate.normalized_value,
                        context,
                        date_only=(
                            candidate is item.due_date_candidate
                            and not candidate.time_was_explicit
                        ),
                    )
                }"
            )
        blocks.append("\n".join(lines))
    return blocks


def format_draft_summary(
    result: DraftAnalysisResult,
    *,
    display: TelegramDisplayContext | None = None,
) -> str:
    return "\n\n".join(format_draft_blocks(result, display=display))


def format_conversion_summary(
    result: DraftConversionResult,
    *,
    display: TelegramDisplayContext,
    workspace: str,
) -> str:
    entries: list[tuple[str, str, datetime | None, bool]] = []
    for item in result.work_items:
        date = item.next_follow_up_at if item.type == "follow_up" else item.due_at
        entries.append(
            (
                item.type,
                item.title,
                date,
                item.type != "follow_up" and date is not None,
            )
        )
    entries.extend(
        ("note", note.content or "Заметка", None, False) for note in result.notes
    )
    count = len(entries)
    if count == 0:
        return "✅ <b>Черновик сохранён</b>"
    if count == 1:
        single_headings = {
            "task": "Задача создана",
            "follow_up": "Follow-up создан",
            "waiting": "Ожидание создано",
            "question": "Вопрос создан",
            "note": "Заметка сохранена",
            "decision": "Решение сохранено",
            "agenda_item": "Пункт повестки создан",
        }
        blocks = [f"✅ <b>{single_headings.get(entries[0][0], 'Запись создана')}</b>"]
    else:
        blocks = [
            f"✅ <b>Создано {count} "
            f"{pluralize(count, ('запись', 'записи', 'записей'))}</b>"
        ]
    for position, (item_type, title, date, may_be_date_only) in enumerate(entries, 1):
        icon, label = item_presentation(item_type)
        prefix = f"{position}. " if count > 1 else ""
        lines = [
            f"{prefix}{icon} <b>{label}</b>",
            html_text(preview(title, 300)),
        ]
        if date is not None:
            date_only = (
                may_be_date_only
                and date.astimezone(display.timezone)
                .time()
                .replace(microsecond=0)
                .isoformat()
                == "23:59:59"
            )
            lines.append(f"📅 {format_datetime(date, display, date_only=date_only)}")
        blocks.append("\n".join(lines))
    blocks.append(f"📂 {WORKSPACE_LABELS[workspace]}")
    return "\n\n".join(blocks)


def due_date_offer_keyboard(
    result: DraftConversionResult,
) -> InlineKeyboardMarkup | None:
    tasks = [
        item
        for item in result.work_items
        if item.type == WorkItemType.TASK.value and item.due_at is None
    ]
    if not tasks:
        return None
    multiple = len(tasks) > 1
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        f"📅 Добавить срок · {preview(item.title, 28)}"
                        if multiple
                        else "📅 Добавить срок"
                    ),
                    callback_data=(
                        f"wi:r:{item.id}:"
                        f"{encode_revision(work_item_revision(item.updated_at))}"
                    ),
                )
            ]
            for item in tasks
        ]
    )


def ready_keyboard(draft_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Сохранить",
                    callback_data=f"draft:confirm:{draft_id}",
                ),
                InlineKeyboardButton(
                    text="✏️ Изменить",
                    callback_data=f"draft:change:{draft_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=f"draft:cancel:{draft_id}",
                )
            ],
        ]
    )


def question_keyboard(
    draft_id: UUID,
    question: ClarificationQuestion,
) -> InlineKeyboardMarkup | None:
    if not question.options:
        return None
    buttons = [
        InlineKeyboardButton(
            text=option.label,
            callback_data=f"draft:answer:{draft_id}:{index}",
        )
        for index, option in enumerate(question.options)
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[button] for button in buttons])


async def send_question(
    message: Message,
    draft: DraftSession,
    question: ClarificationQuestion,
    db_session: AsyncSession,
) -> None:
    keyboard = question_keyboard(draft.id, question)
    sent = await message.answer(
        question.text,
        parse_mode=None,
        reply_markup=keyboard or ForceReply(selective=True),
    )
    await set_question_message_id(db_session, draft, sent.message_id)
    await db_session.commit()


async def show_draft(
    message: Message,
    draft: DraftSession,
    db_session: AsyncSession,
    *,
    display: TelegramDisplayContext | None = None,
) -> None:
    analysis = load_analysis(draft)
    chunks = join_html_blocks(format_draft_blocks(analysis, display=display))
    keyboard: InlineKeyboardMarkup | None = None
    if draft.status == "ready":
        keyboard = ready_keyboard(draft.id)
        question = None
    else:
        question = next_clarification_question(analysis)
        if question is None:
            await transition_draft(db_session, draft, "ready")
            await db_session.commit()
            keyboard = ready_keyboard(draft.id)
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            parse_mode="HTML",
            reply_markup=keyboard if index == len(chunks) - 1 else None,
        )
    if question is not None:
        await send_question(message, draft, question, db_session)


async def analyze_note_content(
    message: Message,
    *,
    content: str,
    telegram_user_id: int,
    source: DraftSource,
    service: DraftParsingService,
    db_session: AsyncSession,
    draft: DraftSession,
    draft_ttl_hours: int,
    precomputed_result: DraftAnalysisResult | None = None,
    active_workspace: str | None = None,
    high_confidence_threshold: float = 0.8,
    draft_conversion_service: DraftConversionService | None = None,
    notification_defaults: NotificationDefaults | None = None,
    failure_message: str = DRAFT_FAILED_MESSAGE,
) -> None:
    preferences: EffectiveNotificationPreferences | None = None
    try:
        workspace = (
            active_workspace
            or draft.workspace
            or (
                precomputed_result.context.active_workspace
                if precomputed_result is not None
                else "personal"
            )
        )
        result = precomputed_result or (
            await service.parse(
                content,
                source=source,
                active_workspace=workspace,
            )
            if workspace is not None
            else await service.parse(content, source=source)
        )
        if result.context.active_workspace != workspace:
            result = result.model_copy(
                update={
                    "context": result.context.model_copy(
                        update={"active_workspace": workspace}
                    )
                }
            )
        if notification_defaults is not None:
            preferences = await get_effective_notification_preferences(
                db_session,
                draft.user_id,
                notification_defaults,
            )
            result = apply_default_reminder_time(
                result,
                default_time=preferences.default_reminder_time,
            )
        question = next_clarification_question(result)
        await replace_draft_analysis(
            db_session,
            draft,
            result,
            question=question,
            ttl_hours=draft_ttl_hours,
        )
        await db_session.commit()
    except AIError as error:
        await mark_draft_failed_safely(db_session, draft)
        logger.warning(
            "telegram_draft_failed user_id=%s category=%s",
            telegram_user_id,
            safe_ai_error_code(error),
        )
        await message.answer(failure_message)
        return
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_draft_database_failed user_id=%s operation=save_analysis",
            telegram_user_id,
        )
        await mark_draft_failed_safely(db_session, draft)
        await message.answer(failure_message)
        return

    if draft_conversion_service is not None and fast_capture_is_ready(
        result,
        high_confidence_threshold=high_confidence_threshold,
    ):
        try:
            conversion_result = await draft_conversion_service.convert(
                db_session,
                draft_id=draft.id,
                user_id=draft.user_id,
            )
            await db_session.commit()
            display = draft_display_context(result, preferences)
            summary = fast_capture_summary(
                result,
                workspace=draft.workspace,
                display=display,
            )
            keyboard = due_date_offer_keyboard(conversion_result)
            if keyboard is None:
                await answer_with_main_menu(message, summary, parse_mode="HTML")
            else:
                await message.answer(
                    summary,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            return
        except (DraftConversionError, SQLAlchemyError):
            await db_session.rollback()
            logger.error(
                "telegram_fast_capture_conversion_failed user_id=%s draft_id=%s",
                telegram_user_id,
                draft.id,
            )
    await show_draft(
        message,
        draft,
        db_session,
        display=draft_display_context(result, preferences),
    )


async def refine_draft(
    message: Message,
    *,
    draft: DraftSession,
    answer: str,
    answer_source: DraftSource,
    update_id: int,
    user_id: UUID,
    telegram_user_id: int,
    service: DraftParsingService,
    db_session: AsyncSession,
    draft_ttl_hours: int,
    already_claimed: bool = False,
) -> None:
    claimed_draft: DraftSession | None = draft
    if not already_claimed:
        claim, claimed_draft = await claim_update(
            db_session,
            draft_id=draft.id,
            user_id=user_id,
            update_id=update_id,
            ttl_hours=draft_ttl_hours,
        )
        if claim == "duplicate":
            await db_session.rollback()
            return
        if claim == "busy":
            await db_session.rollback()
            await message.answer(DRAFT_BUSY_MESSAGE)
            return
        if claim != "claimed" or claimed_draft is None:
            await db_session.rollback()
            await message.answer(DRAFT_EXPIRED_MESSAGE)
            return
    if claimed_draft is None:
        await message.answer(DRAFT_EXPIRED_MESSAGE)
        return
    current_question = claimed_draft.current_question or DRAFT_CHANGE_QUESTION
    current = load_analysis(claimed_draft)
    await enqueue_ai_job(
        db_session,
        user_id=user_id,
        job_kind="draft_refine",
        entity_id=claimed_draft.id,
        operation_key=f"telegram:{update_id}",
        prompt_name="refinement",
        prompt_version=REFINEMENT_PROMPT_VERSION,
        input_text=answer,
        input_source=answer_source.value,
    )
    await db_session.commit()

    try:
        result = await service.refine(
            current,
            answer,
            answer_source=answer_source,
            question=current_question,
        )
        question = next_clarification_question(result)
        await replace_draft_analysis(
            db_session,
            claimed_draft,
            result,
            question=question,
            ttl_hours=draft_ttl_hours,
        )
        await db_session.commit()
    except AIError as error:
        await release_processing_update_safely(db_session, claimed_draft)
        logger.warning(
            "telegram_draft_refinement_failed user_id=%s category=%s",
            telegram_user_id,
            safe_ai_error_code(error),
        )
        await message.answer(DRAFT_FAILED_MESSAGE)
        return
    except SQLAlchemyError:
        await db_session.rollback()
        await release_processing_update_safely(db_session, claimed_draft)
        logger.error(
            "telegram_draft_database_failed user_id=%s operation=refine",
            telegram_user_id,
        )
        await message.answer(DRAFT_FAILED_MESSAGE)
        return

    await show_draft(message, claimed_draft, db_session)
    if question is None:
        await restore_main_menu(message)


def parse_callback_data(data: str | None) -> tuple[str, UUID, int | None] | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) not in {3, 4} or parts[0] != "draft":
        return None
    try:
        draft_id = UUID(parts[2])
        option = int(parts[3]) if len(parts) == 4 else None
    except (ValueError, TypeError):
        return None
    return parts[1], draft_id, option


async def _draft_callback(
    callback_query: CallbackQuery,
    feedback: CallbackFeedback,
    event_update: Update,
    db_session: AsyncSession,
    draft_parsing_service: DraftParsingService | None = None,
    draft_conversion_service: DraftConversionService | None = None,
    draft_ttl_hours: int = 24,
    notification_defaults: NotificationDefaults | None = None,
    app_timezone: ZoneInfo | None = None,
) -> None:
    parsed = parse_callback_data(callback_query.data)
    telegram_user = callback_query.from_user
    if parsed is None or telegram_user is None:
        await feedback.error("Кнопка устарела.")
        return
    await feedback.acknowledge()
    action, draft_id, option_index = parsed
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await db_session.rollback()
        await feedback.error(DRAFT_NOT_FOUND_MESSAGE)
        return
    draft = await get_draft_for_user(
        db_session,
        draft_id,
        user.id,
        for_update=True,
    )
    if draft is None:
        await db_session.rollback()
        await feedback.error(DRAFT_NOT_FOUND_MESSAGE)
        return
    if draft.expires_at <= utc_now() and draft.status in {
        "parsing",
        "needs_clarification",
        "ready",
    }:
        await transition_draft(db_session, draft, "expired")
        await db_session.commit()
        await feedback.error(DRAFT_EXPIRED_MESSAGE)
        return
    if not isinstance(callback_query.message, Message):
        await db_session.rollback()
        await feedback.error("Сообщение недоступно.")
        return

    if action == "cancel" and draft.status in {
        "parsing",
        "needs_clarification",
        "ready",
    }:
        await transition_draft(db_session, draft, "cancelled")
        await db_session.commit()
        await callback_query.message.edit_text(f"🚫 {DRAFT_CANCELLED_MESSAGE}")
        return
    if action == "confirm" and draft.status in {
        "needs_clarification",
        "ready",
        "confirmed",
    }:
        converter = draft_conversion_service or DraftConversionService()
        result = await converter.convert(
            db_session,
            draft_id=draft.id,
            user_id=user.id,
            allow_incomplete=draft.status == "needs_clarification",
        )
        await db_session.commit()
        display = TelegramDisplayContext(timezone=app_timezone or ZoneInfo("UTC"))
        if notification_defaults is not None:
            preferences = await get_effective_notification_preferences(
                db_session, user.id, notification_defaults
            )
            display = TelegramDisplayContext.from_preferences(preferences)
        await callback_query.message.edit_text(
            format_conversion_summary(
                result,
                display=display,
                workspace=draft.workspace,
            ),
            parse_mode="HTML",
            reply_markup=due_date_offer_keyboard(result),
        )
        return
    if action == "change" and draft.status in {"needs_clarification", "ready"}:
        draft.status = "needs_clarification"
        draft.current_question = DRAFT_CHANGE_QUESTION
        draft.current_question_options = []
        draft.current_question_context = {"field": "freeform_change"}
        sent = await callback_query.message.answer(
            DRAFT_CHANGE_QUESTION,
            reply_markup=ForceReply(selective=True),
        )
        await set_question_message_id(db_session, draft, sent.message_id)
        await db_session.commit()
        await feedback.prompt("Жду ваши изменения.", remove_keyboard=True)
        return
    if action == "answer" and option_index is not None:
        if option_index < 0:
            await db_session.rollback()
            await feedback.error("Кнопка устарела.")
            return
        if draft_parsing_service is None or draft.status != "needs_clarification":
            await db_session.rollback()
            await feedback.error(DRAFT_NOT_FOUND_MESSAGE)
            return
        try:
            option = draft.current_question_options[option_index]
        except IndexError:
            await db_session.rollback()
            await feedback.error("Кнопка устарела.")
            return
        if option.get("action") == "confirm":
            converter = draft_conversion_service or DraftConversionService()
            result = await converter.convert(
                db_session,
                draft_id=draft.id,
                user_id=user.id,
                allow_incomplete=True,
            )
            await db_session.commit()
            display = TelegramDisplayContext(timezone=app_timezone or ZoneInfo("UTC"))
            if notification_defaults is not None:
                preferences = await get_effective_notification_preferences(
                    db_session, user.id, notification_defaults
                )
                display = TelegramDisplayContext.from_preferences(preferences)
            await callback_query.message.edit_text(
                format_conversion_summary(
                    result,
                    display=display,
                    workspace=draft.workspace,
                ),
                parse_mode="HTML",
                reply_markup=due_date_offer_keyboard(result),
            )
            return
        if option.get("action") == "change":
            draft.current_question = DRAFT_CHANGE_QUESTION
            draft.current_question_options = []
            sent = await callback_query.message.answer(
                DRAFT_CHANGE_QUESTION,
                reply_markup=ForceReply(selective=True),
            )
            await set_question_message_id(db_session, draft, sent.message_id)
            await db_session.commit()
            await feedback.prompt("Жду ваши изменения.", remove_keyboard=True)
            return
        if option.get("action") == "cancel":
            await transition_draft(db_session, draft, "cancelled")
            await db_session.commit()
            await callback_query.message.edit_text(f"🚫 {DRAFT_CANCELLED_MESSAGE}")
            return
        await refine_draft(
            callback_query.message,
            draft=draft,
            answer=option["value"],
            answer_source=DraftSource.TEXT,
            update_id=event_update.update_id,
            user_id=user.id,
            telegram_user_id=telegram_user.id,
            service=draft_parsing_service,
            db_session=db_session,
            draft_ttl_hours=draft_ttl_hours,
        )
        await feedback.success("Ответ учтён.", remove_keyboard=True)
        return

    await db_session.rollback()
    await feedback.error("Действие недоступно.")


async def draft_callback(
    callback_query: CallbackQuery,
    event_update: Update,
    db_session: AsyncSession,
    draft_parsing_service: DraftParsingService | None = None,
    draft_conversion_service: DraftConversionService | None = None,
    draft_ttl_hours: int = 24,
    notification_defaults: NotificationDefaults | None = None,
    app_timezone: ZoneInfo | None = None,
) -> None:
    feedback = CallbackFeedback(callback_query)
    try:
        await _draft_callback(
            callback_query,
            feedback,
            event_update,
            db_session,
            draft_parsing_service,
            draft_conversion_service,
            draft_ttl_hours,
            notification_defaults,
            app_timezone,
        )
    except (DraftConversionError, SQLAlchemyError) as error:
        await db_session.rollback()
        parsed = parse_callback_data(callback_query.data)
        logger.error(
            "telegram_draft_conversion_failed user_id=%s draft_id=%s category=%s",
            callback_query.from_user.id,
            parsed[1] if parsed is not None else "unknown",
            type(error).__name__,
        )
        await feedback.error(DRAFT_CONVERSION_FAILED_MESSAGE)
