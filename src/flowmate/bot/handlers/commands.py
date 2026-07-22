from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowmate.bot.filters import (
    ActiveDraftFilter,
    ActiveMeetingCaptureFilter,
    ActiveWorkItemActionFilter,
    MeetingReviewReplyFilter,
    MeetingTitleReplyFilter,
)
from flowmate.bot.handlers.clarification import active_draft_message
from flowmate.bot.handlers.drafts import (
    DRAFT_CANCELLED_MESSAGE,
    DRAFT_NOT_FOUND_MESSAGE,
    draft_callback,
    show_draft,
)
from flowmate.bot.handlers.meeting_capture import (
    meeting_capture_undo_callback,
    meeting_notes_command,
    meeting_text_capture,
    meeting_voice_capture,
)
from flowmate.bot.handlers.meeting_review import (
    meeting_review_callback,
    meeting_review_command,
    meeting_review_reply,
)
from flowmate.bot.handlers.meetings import (
    meeting_callback,
    meeting_cancel_command,
    meeting_command,
    meeting_end_command,
    meeting_status_command,
    meeting_title_reply,
)
from flowmate.bot.handlers.navigation import (
    FOLLOW_UPS_BUTTON,
    PEOPLE_BUTTON,
    QUESTIONS_BUTTON,
    RECORD_BUTTON,
    SEARCH_BUTTON,
    SETTINGS_BUTTON,
    TASKS_BUTTON,
    TODAY_BUTTON,
    TOPICS_BUTTON,
    WAITING_BUTTON,
    followups_command,
    list_callback,
    main_menu_keyboard,
    menu_callback,
    menu_command,
    people_command,
    questions_command,
    record_prompt,
    search_callback,
    search_command,
    tasks_command,
    today_command,
    topics_command,
    waiting_command,
)
from flowmate.bot.handlers.notes import notes_command, text_note
from flowmate.bot.handlers.preferences import (
    quiet_command,
    reminders_settings_command,
    snooze_command,
)
from flowmate.bot.handlers.reminders import digest_callback, reminder_callback
from flowmate.bot.handlers.voice import voice_message
from flowmate.bot.handlers.work_items import (
    action_session_message,
    work_item_callback,
    work_item_selection_callback,
)
from flowmate.bot.middleware import (
    AllowedUserMiddleware,
    DatabaseSessionMiddleware,
    PersistentUpdateMiddleware,
)
from flowmate.db.drafts import get_active_draft_for_user, transition_draft
from flowmate.db.health import database_is_ready
from flowmate.db.users import get_or_create_telegram_user, get_user_by_telegram_id
from flowmate.task_engine.action_sessions import (
    finish_action_session,
    get_active_action_session,
)


async def start_command(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return

    user, _ = await get_or_create_telegram_user(
        db_session,
        telegram_user.id,
        display_name=telegram_user.full_name[:255],
    )
    user.display_name = telegram_user.full_name[:255]
    user.is_active = True
    await db_session.flush()
    await message.answer(
        "Добро пожаловать! FlowMate готов к работе.",
        parse_mode=None,
        reply_markup=main_menu_keyboard(),
    )


async def help_command(message: Message) -> None:
    await message.answer(
        "Доступные команды: /start, /menu, /help, /status, /notes, /search. "
        "Записи: /today, /tasks, /followups, /waiting, /questions, "
        "/topics, /people. Черновики: /draft, /cancel. "
        "Напоминания: /reminders, /quiet, /snooze. "
        "Встречи: /meeting, /meeting_status, /meeting_notes, /meeting_end, "
        "/meeting_review, "
        "/meeting_cancel. "
        "Отправьте текст или голосовое сообщение, чтобы сохранить заметку."
    )


async def status_command(message: Message, db_engine: AsyncEngine) -> None:
    if await database_is_ready(db_engine):
        await message.answer("Бот работает, база данных доступна.")
        return
    await message.answer("Сервис временно недоступен. Попробуйте позже.")


async def unsupported_message(message: Message) -> None:
    await message.answer("Отправьте текст, голосовое сообщение или используйте /help.")


async def draft_command(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    draft = (
        await get_active_draft_for_user(db_session, user.id)
        if user is not None
        else None
    )
    if draft is None:
        await message.answer(DRAFT_NOT_FOUND_MESSAGE)
        return
    if draft.status == "parsing" or draft.analysis_payload is None:
        await message.answer("Черновик ещё анализируется.")
        return
    await show_draft(message, draft, db_session)


async def cancel_command(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    action_session = (
        await get_active_action_session(db_session, user.id)
        if user is not None
        else None
    )
    if action_session is not None:
        await finish_action_session(db_session, action_session, status="cancelled")
        await db_session.commit()
        await message.answer("Текущее действие отменено.")
        return
    draft = (
        await get_active_draft_for_user(db_session, user.id)
        if user is not None
        else None
    )
    if draft is None:
        await message.answer(DRAFT_NOT_FOUND_MESSAGE)
        return
    await transition_draft(db_session, draft, "cancelled")
    await db_session.commit()
    await message.answer(DRAFT_CANCELLED_MESSAGE)


def create_router(
    allowed_user_ids: frozenset[int],
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
) -> Router:
    router = Router(name="flowmate")
    router.message.outer_middleware(AllowedUserMiddleware(allowed_user_ids))
    router.callback_query.outer_middleware(AllowedUserMiddleware(allowed_user_ids))
    # Filters query persistent dialog state, so the session must exist before
    # aiogram evaluates them rather than only around the selected handler.
    router.message.outer_middleware(DatabaseSessionMiddleware(session_factory, engine))
    router.callback_query.outer_middleware(
        DatabaseSessionMiddleware(session_factory, engine)
    )
    router.message.outer_middleware(PersistentUpdateMiddleware())
    router.callback_query.outer_middleware(PersistentUpdateMiddleware())
    router.message.register(start_command, Command("start"))
    router.message.register(menu_command, Command("menu"))
    router.message.register(help_command, Command("help"))
    router.message.register(status_command, Command("status"))
    router.message.register(notes_command, Command("notes"))
    router.message.register(draft_command, Command("draft"))
    router.message.register(cancel_command, Command("cancel"))
    router.message.register(today_command, Command("today"))
    router.message.register(tasks_command, Command("tasks"))
    router.message.register(followups_command, Command("followups"))
    router.message.register(waiting_command, Command("waiting"))
    router.message.register(questions_command, Command("questions"))
    router.message.register(topics_command, Command("topics"))
    router.message.register(people_command, Command("people"))
    router.message.register(reminders_settings_command, Command("reminders"))
    router.message.register(quiet_command, Command("quiet"))
    router.message.register(snooze_command, Command("snooze"))
    router.message.register(search_command, Command("search"))
    router.message.register(meeting_command, Command("meeting"))
    router.message.register(meeting_status_command, Command("meeting_status"))
    router.message.register(meeting_notes_command, Command("meeting_notes"))
    router.message.register(meeting_end_command, Command("meeting_end"))
    router.message.register(meeting_review_command, Command("meeting_review"))
    router.message.register(meeting_cancel_command, Command("meeting_cancel"))
    router.message.register(record_prompt, F.text == RECORD_BUTTON)
    router.message.register(today_command, F.text == TODAY_BUTTON)
    router.message.register(tasks_command, F.text == TASKS_BUTTON)
    router.message.register(followups_command, F.text == FOLLOW_UPS_BUTTON)
    router.message.register(waiting_command, F.text == WAITING_BUTTON)
    router.message.register(questions_command, F.text == QUESTIONS_BUTTON)
    router.message.register(people_command, F.text == PEOPLE_BUTTON)
    router.message.register(topics_command, F.text == TOPICS_BUTTON)
    router.message.register(search_command, F.text == SEARCH_BUTTON)
    router.message.register(reminders_settings_command, F.text == SETTINGS_BUTTON)
    router.message.register(
        meeting_title_reply,
        MeetingTitleReplyFilter(),
        F.text,
    )
    router.message.register(
        meeting_review_reply,
        MeetingReviewReplyFilter(),
        F.text | F.voice,
    )
    router.message.register(
        action_session_message,
        ActiveWorkItemActionFilter(),
        F.text | F.voice,
    )
    router.message.register(
        active_draft_message,
        ActiveDraftFilter(),
        F.text | F.voice,
    )
    router.message.register(
        meeting_voice_capture,
        ActiveMeetingCaptureFilter(),
        F.voice,
    )
    router.message.register(
        meeting_text_capture,
        ActiveMeetingCaptureFilter(),
        F.text & ~F.text.startswith("/"),
    )
    router.message.register(voice_message, F.voice)
    router.message.register(text_note, F.text & ~F.text.startswith("/"))
    router.message.register(unsupported_message)
    router.callback_query.register(draft_callback, F.data.startswith("draft:"))
    router.callback_query.register(reminder_callback, F.data.startswith("rem:"))
    router.callback_query.register(digest_callback, F.data.startswith("dig:"))
    router.callback_query.register(list_callback, F.data.startswith("ls:"))
    router.callback_query.register(search_callback, F.data.startswith("lq:"))
    router.callback_query.register(menu_callback, F.data == "nav:menu")
    router.callback_query.register(work_item_callback, F.data.startswith("wi:"))
    router.callback_query.register(meeting_callback, F.data.startswith("mt:"))
    router.callback_query.register(
        meeting_capture_undo_callback, F.data.startswith("mc:undo:")
    )
    router.callback_query.register(meeting_review_callback, F.data.startswith("mr:"))
    router.callback_query.register(
        work_item_selection_callback,
        F.data.startswith("wis:"),
    )
    return router
