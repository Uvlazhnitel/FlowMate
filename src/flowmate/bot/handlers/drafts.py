# ruff: noqa: RUF001
import logging
from uuid import UUID

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

from flowmate.ai.errors import AIError
from flowmate.ai.schemas import (
    DependencyCandidate,
    DependencyRelation,
    DraftAnalysisResult,
    DraftItem,
    DraftItemType,
    DraftReadiness,
    DraftSource,
    TemporalCandidate,
    TemporalStatus,
)
from flowmate.ai.service import DraftParsingService
from flowmate.bot.formatting import split_plain_text
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
from flowmate.task_engine.conversion import (
    DraftConversionError,
    DraftConversionService,
    conversion_summary,
)

DRAFT_ANALYZING_MESSAGE = "Заметка сохранена. Анализирую содержание."
DRAFT_FAILED_MESSAGE = "Не удалось подготовить структурированный черновик."
DRAFT_CONTROL_MESSAGE = "Проверьте черновик. Финальные записи ещё не созданы."
DRAFT_CANCELLED_MESSAGE = "Черновик отменён. Исходная заметка сохранена."
DRAFT_CONVERSION_FAILED_MESSAGE = (
    "Не удалось создать записи из черновика. Черновик сохранён."
)
DRAFT_EXPIRED_MESSAGE = "Срок действия черновика истёк."
DRAFT_NOT_FOUND_MESSAGE = "Активный черновик не найден."
DRAFT_BUSY_MESSAGE = "Предыдущий ответ ещё обрабатывается."
DRAFT_REPLY_REQUIRED_MESSAGE = "Ответьте на текущее уточнение через Reply."
DRAFT_CHANGE_QUESTION = "Что изменить в черновике?"

ITEM_TYPE_LABELS = {
    DraftItemType.TASK: "задача",
    DraftItemType.FOLLOW_UP: "контроль",
    DraftItemType.WAITING: "ожидание",
    DraftItemType.QUESTION: "вопрос",
    DraftItemType.NOTE: "заметка",
    DraftItemType.DECISION: "решение",
    DraftItemType.AGENDA_ITEM: "пункт повестки",
    DraftItemType.UNKNOWN: "не определено",
}
READINESS_LABELS = {
    DraftReadiness.READY: "готово",
    DraftReadiness.CLARIFICATION_REQUIRED: "нужно уточнение",
    DraftReadiness.UNRESOLVED: "не определено",
}
TEMPORAL_STATUS_LABELS = {
    TemporalStatus.RESOLVED: "определено",
    TemporalStatus.AMBIGUOUS: "неоднозначно",
    TemporalStatus.INVALID: "некорректно",
}

logger = logging.getLogger(__name__)


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


def normalize_display_text(value: str) -> str:
    return " ".join(value.split())


def format_temporal_candidate(label: str, candidate: TemporalCandidate) -> str:
    original = normalize_display_text(candidate.original_phrase)
    value = (
        candidate.normalized_value.isoformat()
        if candidate.normalized_value is not None
        else TEMPORAL_STATUS_LABELS[candidate.status]
    )
    explanation = (
        f" ({normalize_display_text(candidate.explanation)})"
        if candidate.explanation
        else ""
    )
    return f'{label}: "{original}" → {value}{explanation}'


def format_dependency(dependency: DependencyCandidate) -> str:
    phrase = normalize_display_text(dependency.original_phrase)
    if dependency.relation is DependencyRelation.CONDITIONAL:
        condition = normalize_display_text(dependency.condition or "")
        return f'если "{condition}" ({phrase})'
    relation_labels = {
        DependencyRelation.BEFORE: "до",
        DependencyRelation.AFTER: "после",
        DependencyRelation.BLOCKED_BY: "заблокировано до",
        DependencyRelation.WAITING_FOR: "ожидает",
    }
    relation = relation_labels[dependency.relation]
    return f'{relation} пункта {dependency.target_item_number} ("{phrase}")'


def append_optional_item_fields(lines: list[str], item: DraftItem) -> None:
    if item.description:
        lines.append(f"Описание: {normalize_display_text(item.description)}")
    if item.person_candidates:
        people = ", ".join(map(normalize_display_text, item.person_candidates))
        lines.append(f"Люди: {people}")
    if item.topic_candidates:
        topics = ", ".join(map(normalize_display_text, item.topic_candidates))
        lines.append(f"Темы: {topics}")
    if item.due_date_candidate:
        lines.append(format_temporal_candidate("Срок", item.due_date_candidate))
    if item.reminder_candidate:
        lines.append(format_temporal_candidate("Напоминание", item.reminder_candidate))
    if item.dependencies:
        dependencies = "; ".join(map(format_dependency, item.dependencies))
        lines.append(f"Зависимости: {dependencies}")
    if item.notes:
        lines.append(
            f"Примечания: {'; '.join(map(normalize_display_text, item.notes))}"
        )
    if item.missing_fields:
        missing = ", ".join(map(normalize_display_text, item.missing_fields))
        lines.append(f"Не хватает данных: {missing}")
    if item.ambiguities:
        values = "; ".join(map(normalize_display_text, item.ambiguities))
        lines.append(f"Неоднозначности: {values}")


def format_draft_summary(result: DraftAnalysisResult) -> str:
    lines = [
        f"Я нашёл записей: {len(result.items)}",
        f"Намерение: {ITEM_TYPE_LABELS[result.overall_intent]}",
        f"Общая уверенность: {round(result.confidence * 100)}%",
    ]
    for position, assessment in enumerate(result.items, start=1):
        item = assessment.item
        lines.extend(
            (
                "",
                f"{position}. [{ITEM_TYPE_LABELS[item.type]}] "
                f"{normalize_display_text(item.title)}",
                f"Статус: {READINESS_LABELS[assessment.readiness]}",
                f"Уверенность: {round(item.confidence * 100)}%",
            )
        )
        append_optional_item_fields(lines, item)
    if result.ambiguities:
        values = "; ".join(map(normalize_display_text, result.ambiguities))
        lines.extend(("", f"Общие неоднозначности: {values}"))
    return "\n".join(lines)


def ready_keyboard(draft_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=f"draft:confirm:{draft_id}",
                ),
                InlineKeyboardButton(
                    text="Изменить",
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
) -> None:
    analysis = load_analysis(draft)
    for chunk in split_plain_text(format_draft_summary(analysis)):
        await message.answer(chunk, parse_mode=None)
    if draft.status == "ready":
        await message.answer(
            DRAFT_CONTROL_MESSAGE,
            reply_markup=ready_keyboard(draft.id),
        )
        return
    question = next_clarification_question(analysis)
    if question is None:
        await transition_draft(db_session, draft, "ready")
        await db_session.commit()
        await message.answer(
            DRAFT_CONTROL_MESSAGE,
            reply_markup=ready_keyboard(draft.id),
        )
        return
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
) -> None:
    try:
        result = precomputed_result or await service.parse(content, source=source)
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
            type(error).__name__,
        )
        await message.answer(DRAFT_FAILED_MESSAGE)
        return
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_draft_database_failed user_id=%s operation=save_analysis",
            telegram_user_id,
        )
        await mark_draft_failed_safely(db_session, draft)
        await message.answer(DRAFT_FAILED_MESSAGE)
        return

    await show_draft(message, draft, db_session)


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
            type(error).__name__,
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
    event_update: Update,
    db_session: AsyncSession,
    draft_parsing_service: DraftParsingService | None = None,
    draft_conversion_service: DraftConversionService | None = None,
    draft_ttl_hours: int = 24,
) -> None:
    parsed = parse_callback_data(callback_query.data)
    telegram_user = callback_query.from_user
    if parsed is None or telegram_user is None:
        await callback_query.answer("Кнопка устарела.", show_alert=True)
        return
    action, draft_id, option_index = parsed
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await db_session.rollback()
        await callback_query.answer(DRAFT_NOT_FOUND_MESSAGE, show_alert=True)
        return
    draft = await get_draft_for_user(
        db_session,
        draft_id,
        user.id,
        for_update=True,
    )
    if draft is None:
        await db_session.rollback()
        await callback_query.answer(DRAFT_NOT_FOUND_MESSAGE, show_alert=True)
        return
    if draft.expires_at <= utc_now() and draft.status in {
        "parsing",
        "needs_clarification",
        "ready",
    }:
        await transition_draft(db_session, draft, "expired")
        await db_session.commit()
        await callback_query.answer(DRAFT_EXPIRED_MESSAGE, show_alert=True)
        return
    if not isinstance(callback_query.message, Message):
        await db_session.rollback()
        await callback_query.answer("Сообщение недоступно.", show_alert=True)
        return

    if action == "cancel" and draft.status in {
        "parsing",
        "needs_clarification",
        "ready",
    }:
        await transition_draft(db_session, draft, "cancelled")
        await db_session.commit()
        await callback_query.answer()
        await callback_query.message.edit_text(DRAFT_CANCELLED_MESSAGE)
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
        await callback_query.answer()
        await callback_query.message.edit_text(conversion_summary(result))
        return
    if action == "change" and draft.status in {"needs_clarification", "ready"}:
        draft.status = "needs_clarification"
        draft.current_question = DRAFT_CHANGE_QUESTION
        draft.current_question_options = []
        draft.current_question_context = {"field": "freeform_change"}
        await callback_query.answer()
        sent = await callback_query.message.answer(
            DRAFT_CHANGE_QUESTION,
            reply_markup=ForceReply(selective=True),
        )
        await set_question_message_id(db_session, draft, sent.message_id)
        await db_session.commit()
        return
    if action == "answer" and option_index is not None:
        if option_index < 0:
            await db_session.rollback()
            await callback_query.answer("Кнопка устарела.", show_alert=True)
            return
        if draft_parsing_service is None or draft.status != "needs_clarification":
            await db_session.rollback()
            await callback_query.answer(DRAFT_NOT_FOUND_MESSAGE, show_alert=True)
            return
        try:
            option = draft.current_question_options[option_index]
        except IndexError:
            await db_session.rollback()
            await callback_query.answer("Кнопка устарела.", show_alert=True)
            return
        await callback_query.answer()
        if option.get("action") == "confirm":
            converter = draft_conversion_service or DraftConversionService()
            result = await converter.convert(
                db_session,
                draft_id=draft.id,
                user_id=user.id,
                allow_incomplete=True,
            )
            await db_session.commit()
            await callback_query.message.edit_text(conversion_summary(result))
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
        return

    await db_session.rollback()
    await callback_query.answer("Действие недоступно.", show_alert=True)


async def draft_callback(
    callback_query: CallbackQuery,
    event_update: Update,
    db_session: AsyncSession,
    draft_parsing_service: DraftParsingService | None = None,
    draft_conversion_service: DraftConversionService | None = None,
    draft_ttl_hours: int = 24,
) -> None:
    try:
        await _draft_callback(
            callback_query,
            event_update,
            db_session,
            draft_parsing_service,
            draft_conversion_service,
            draft_ttl_hours,
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
        await callback_query.answer(
            DRAFT_CONVERSION_FAILED_MESSAGE,
            show_alert=True,
        )
