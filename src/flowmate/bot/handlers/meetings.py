# ruff: noqa: RUF001
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
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.provider import MeetingReviewProvider
from flowmate.bot.handlers.meeting_review import send_review_summary
from flowmate.bot.menu import answer_with_main_menu, restore_main_menu
from flowmate.db.models import Meeting, MeetingSetupSession, Person, Topic
from flowmate.db.users import get_user_by_telegram_id
from flowmate.meetings.enums import MeetingType
from flowmate.meetings.review import MeetingReviewError, generate_review, get_review
from flowmate.meetings.service import (
    ActiveMeetingExistsError,
    add_participant,
    cancel_meeting,
    create_meeting,
    default_meeting_title,
    end_meeting,
    get_active_meeting,
    link_topic,
    meeting_is_long_running,
    meeting_now,
    start_meeting,
)
from flowmate.meetings.setup import (
    claim_setup_update,
    finish_setup,
    get_open_setup,
    open_setup,
    update_setup,
)
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)

TYPE_LABELS = {
    MeetingType.LEAD: "С руководителем",
    MeetingType.TEAM: "Командная",
    MeetingType.CLIENT_SYNC: "Клиентский sync",
    MeetingType.STEERING: "Steering",
    MeetingType.ONE_TO_ONE: "Один на один",
    MeetingType.OTHER: "Другая",
}
PAGE_SIZE = 6


def type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"mt:type:{value.value}")]
            for value, label in TYPE_LABELS.items()
        ]
        + [[InlineKeyboardButton(text="Отмена", callback_data="mt:abort")]]
    )


def setup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Название", callback_data="mt:title")],
            [InlineKeyboardButton(text="Участники", callback_data="mt:people:0")],
            [InlineKeyboardButton(text="Темы", callback_data="mt:topics:0")],
            [
                InlineKeyboardButton(
                    text="Проверить и начать", callback_data="mt:review"
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="mt:abort")],
        ]
    )


def format_meeting(meeting: Meeting) -> str:
    title = meeting.title
    status = meeting.status
    started = meeting.started_at
    started_text = started.strftime("%d.%m.%Y %H:%M UTC") if started else "не начата"
    warning = (
        "\n⚠️ Встреча активна больше 12 часов."
        if meeting_is_long_running(meeting)
        else ""
    )
    return f"{title}\nСтатус: {status}\nНачало: {started_text}{warning}"


async def _setup_summary(session: AsyncSession, setup: MeetingSetupSession) -> str:
    context = setup.context
    meeting_type = MeetingType(str(context["type"]))
    person_ids = [UUID(value) for value in context.get("participant_ids", [])]
    topic_ids = [UUID(value) for value in context.get("topic_ids", [])]
    people = (
        list(
            await session.scalars(
                select(Person)
                .where(Person.id.in_(person_ids))
                .order_by(Person.display_name)
            )
        )
        if person_ids
        else []
    )
    topics = (
        list(
            await session.scalars(
                select(Topic).where(Topic.id.in_(topic_ids)).order_by(Topic.name)
            )
        )
        if topic_ids
        else []
    )
    people_text = ", ".join(value.display_name for value in people) or "не выбраны"
    topics_text = ", ".join(value.name for value in topics) or "не выбраны"
    return (
        f"Тип: {TYPE_LABELS[meeting_type]}\n"
        f"Название: {context.get('title') or 'будет создано автоматически'}\n"
        f"Участники: {people_text}\n"
        f"Темы: {topics_text}"
    )


async def meeting_command(
    message: Message,
    db_session: AsyncSession,
    work_item_action_ttl_minutes: int,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await message.answer("Сначала используйте /start.")
        return
    active = await get_active_meeting(db_session, user.id)
    if active is not None:
        await message.answer("Meeting Mode уже активен.\n" + format_meeting(active))
        return
    setup = await open_setup(
        db_session, user.id, ttl_minutes=work_item_action_ttl_minutes
    )
    await db_session.commit()
    if "type" in setup.context:
        await message.answer(
            await _setup_summary(db_session, setup), reply_markup=setup_keyboard()
        )
    else:
        await message.answer("Выберите тип встречи.", reply_markup=type_keyboard())


async def meeting_status_command(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await message.answer("Активной встречи нет.")
        return
    active = await get_active_meeting(db_session, user.id)
    await message.answer(
        "Активной встречи нет."
        if active is None
        else "Meeting Mode активен.\n" + format_meeting(active)
    )


async def meeting_end_command(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    meeting_review_provider: MeetingReviewProvider | None,
    ai_high_confidence_threshold: float,
    ai_clarification_confidence_threshold: float,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await message.answer("Активной встречи нет.")
        return
    active = await get_active_meeting(db_session, user.id)
    if active is None:
        await message.answer("Активной встречи нет.")
        return
    try:
        meeting = await end_meeting(
            db_session, user.id, active.id, telegram_update_id=event_update.update_id
        )
        meeting_id = meeting.id
        await db_session.commit()
        await message.answer("Встреча завершена. Обработка начата.")
        await generate_review(
            db_session,
            user.id,
            meeting_id,
            meeting_review_provider,
            high_threshold=ai_high_confidence_threshold,
            clarification_threshold=ai_clarification_confidence_threshold,
        )
        await db_session.commit()
        review = await get_review(db_session, user.id, meeting_id)
        if review is not None:
            await send_review_summary(message, db_session, review)
    except MeetingReviewError:
        await db_session.commit()
        await message.answer(
            "Встреча сохранена. Итог пока не собран; повторите через /meeting_review."
        )
    except (ValueError, SQLAlchemyError):
        await db_session.rollback()
        await message.answer("Не удалось завершить встречу.")


async def meeting_cancel_command(
    message: Message, event_update: Update, db_session: AsyncSession
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await message.answer("Активной встречи нет.")
        return
    setup = await get_open_setup(db_session, user.id)
    if setup is not None:
        await finish_setup(db_session, setup, status="cancelled")
        await db_session.commit()
        await message.answer("Настройка встречи отменена.")
        return
    active = await get_active_meeting(db_session, user.id)
    if active is None:
        await message.answer("Активной встречи нет.")
        return
    try:
        await cancel_meeting(
            db_session, user.id, active.id, telegram_update_id=event_update.update_id
        )
        await db_session.commit()
        await message.answer("Встреча отменена.")
    except (ValueError, SQLAlchemyError):
        await db_session.rollback()
        await message.answer("Не удалось отменить встречу.")


async def meeting_title_reply(
    message: Message,
    db_session: AsyncSession,
    meeting_setup: MeetingSetupSession,
) -> None:
    title = " ".join((message.text or "").split())
    if not title or len(title) > 500:
        await message.answer("Название должно содержать от 1 до 500 символов.")
        return
    await update_setup(
        db_session, meeting_setup, step="context", values={"title": title}
    )
    await db_session.commit()
    await message.answer(
        await _setup_summary(db_session, meeting_setup), reply_markup=setup_keyboard()
    )
    await restore_main_menu(message)


async def _selection_keyboard(
    session: AsyncSession,
    setup: MeetingSetupSession,
    *,
    kind: str,
    page: int,
) -> InlineKeyboardMarkup:
    rows: list[tuple[UUID, str]]
    if kind == "people":
        person_rows = (
            await session.execute(
                select(Person.id, Person.display_name)
                .where(Person.user_id == setup.user_id, Person.is_active.is_(True))
                .order_by(Person.display_name)
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE + 1)
            )
        ).all()
        rows = [(person_id, label) for person_id, label in person_rows]
    else:
        topic_rows = (
            await session.execute(
                select(Topic.id, Topic.name)
                .where(Topic.user_id == setup.user_id, Topic.is_active.is_(True))
                .order_by(Topic.name)
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE + 1)
            )
        ).all()
        rows = [(topic_id, label) for topic_id, label in topic_rows]
    key = "participant_ids" if kind == "people" else "topic_ids"
    selected = set(setup.context.get(key, []))
    prefix = "p" if kind == "people" else "t"
    buttons: list[list[InlineKeyboardButton]] = []
    for value_id, label in rows[:PAGE_SIZE]:
        marker = "✓ " if str(value_id) in selected else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{label}",
                    callback_data=f"mt:{prefix}:{page}:{value_id}",
                )
            ]
        )
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="Назад", callback_data=f"mt:{kind}:{page - 1}")
        )
    if len(rows) > PAGE_SIZE:
        navigation.append(
            InlineKeyboardButton(text="Вперёд", callback_data=f"mt:{kind}:{page + 1}")
        )
    if navigation:
        buttons.append(navigation)
    buttons.append([InlineKeyboardButton(text="Готово", callback_data="mt:context")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def meeting_callback(
    callback: CallbackQuery,
    event_update: Update,
    db_session: AsyncSession,
    notification_defaults: NotificationDefaults,
) -> None:
    telegram_user = callback.from_user
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await callback.answer("Сначала используйте /start.", show_alert=True)
        return
    setup = await get_open_setup(db_session, user.id)
    if setup is None:
        active = await get_active_meeting(db_session, user.id)
        await callback.answer(
            "Meeting Mode уже активен." if active else "Сессия настройки истекла.",
            show_alert=True,
        )
        return
    if not await claim_setup_update(db_session, setup, event_update.update_id):
        await callback.answer("Уже обработано.")
        return
    data = callback.data or ""
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    try:
        if data.startswith("mt:type:"):
            value = MeetingType(data.rsplit(":", 1)[1])
            await update_setup(
                db_session, setup, step="context", values={"type": value.value}
            )
            await message.answer(
                await _setup_summary(db_session, setup), reply_markup=setup_keyboard()
            )
        elif data == "mt:title":
            prompt = await message.answer(
                "Ответьте названием встречи.", reply_markup=ForceReply(selective=True)
            )
            setup.prompt_message_id = prompt.message_id
            await update_setup(db_session, setup, step="title")
        elif data.startswith("mt:people:") or data.startswith("mt:topics:"):
            kind, page_text = data.split(":")[1:]
            page = max(0, int(page_text))
            await message.answer(
                "Выберите значения.",
                reply_markup=await _selection_keyboard(
                    db_session, setup, kind=kind, page=page
                ),
            )
        elif data.startswith("mt:p:") or data.startswith("mt:t:"):
            _, kind, page_text, raw_id = data.split(":")
            key = "participant_ids" if kind == "p" else "topic_ids"
            values = list(setup.context.get(key, []))
            values.remove(raw_id) if raw_id in values else values.append(raw_id)
            updates: dict[str, object] = {key: values}
            if (
                key == "topic_ids"
                and setup.context.get("primary_topic_id") not in values
            ):
                updates["primary_topic_id"] = values[0] if values else None
            await update_setup(db_session, setup, values=updates)
            selection_kind = "people" if kind == "p" else "topics"
            await message.answer(
                "Выбор обновлён.",
                reply_markup=await _selection_keyboard(
                    db_session, setup, kind=selection_kind, page=int(page_text)
                ),
            )
        elif data == "mt:context":
            await update_setup(db_session, setup, step="context")
            await message.answer(
                await _setup_summary(db_session, setup), reply_markup=setup_keyboard()
            )
        elif data == "mt:review":
            await message.answer(
                await _setup_summary(db_session, setup),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Начать", callback_data="mt:confirm"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="Назад", callback_data="mt:context"
                            )
                        ],
                    ]
                ),
            )
        elif data == "mt:abort":
            await finish_setup(db_session, setup, status="cancelled")
            await answer_with_main_menu(message, "Настройка встречи отменена.")
        elif data == "mt:confirm":
            now = meeting_now()
            meeting_type = MeetingType(str(setup.context["type"]))
            preferences = await get_effective_notification_preferences(
                db_session, user.id, notification_defaults
            )
            title = str(
                setup.context.get("title")
                or default_meeting_title(
                    meeting_type, now, ZoneInfo(preferences.timezone)
                )
            )
            meeting = await create_meeting(
                db_session,
                user.id,
                meeting_type,
                title,
                telegram_update_id=event_update.update_id,
                now=now,
            )
            for value in setup.context.get("participant_ids", []):
                await add_participant(db_session, user.id, meeting.id, UUID(value))
            for value in setup.context.get("topic_ids", []):
                await link_topic(
                    db_session,
                    user.id,
                    meeting.id,
                    UUID(value),
                    primary=value == setup.context.get("primary_topic_id"),
                )
            meeting = await start_meeting(
                db_session,
                user.id,
                meeting.id,
                telegram_update_id=event_update.update_id,
                now=now,
            )
            await finish_setup(
                db_session, setup, status="completed", meeting_id=meeting.id
            )
            await answer_with_main_menu(
                message, "Meeting Mode активен.\n" + format_meeting(meeting)
            )
        await db_session.commit()
        await callback.answer()
    except (ValueError, KeyError, ActiveMeetingExistsError, SQLAlchemyError):
        await db_session.rollback()
        await callback.answer("Не удалось обновить Meeting Mode.", show_alert=True)
