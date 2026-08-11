# ruff: noqa: RUF001

from aiogram.types import (
    CallbackQuery,
    Message,
    Update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.callback_feedback import CallbackFeedback
from flowmate.bot.menu import main_menu_keyboard
from flowmate.db.users import get_user_by_telegram_id
from flowmate.task_engine.action_sessions import (
    create_action_session,
)
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.queries import (
    PersonScope,
)
from flowmate.task_engine.transient_dialogs import cancel_transient_dialogs

PAGE_SIZE = 5
MAX_PAGE = 999
MAX_TITLE_LENGTH = 120
EXPIRED_LIST_MESSAGE = "Список устарел. Откройте его заново."
LIST_FAILED_MESSAGE = "Не удалось загрузить список. Попробуйте позже."

VIEW_HEADINGS = {
    "d": "📅 Просрочено и на сегодня",
    "n": "📆 На завтра",
    "t": "✅ Активные задачи",
    "f": "🔁 Активные follow-up",
    "w": "⏳ Ожидания",
    "q": "❓ Открытые вопросы",
    "p": "👥 Люди",
    "o": "🗂 Активные темы",
    "s": "🔍 Результаты поиска",
}

PEOPLE_SCOPE_LABELS: dict[PersonScope, str] = {
    "work": "В работе",
    "recent": "Недавние",
    "all": "Все",
}


async def menu_command(
    message: Message,
    db_session: AsyncSession | None = None,
) -> None:
    if db_session is not None and message.from_user is not None:
        user = await get_user_by_telegram_id(db_session, message.from_user.id)
        if user is not None:
            await cancel_transient_dialogs(db_session, user.id)
            await db_session.commit()
    await message.answer(
        "Главное меню FlowMate.",
        parse_mode=None,
        reply_markup=main_menu_keyboard(),
    )


async def record_prompt(
    message: Message,
    event_update: Update,
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
    await cancel_transient_dialogs(db_session, user.id)
    await create_action_session(
        db_session,
        user.id,
        action=WorkItemAction.CAPTURE_NEW,
        ttl_minutes=work_item_action_ttl_minutes,
        telegram_update_id=event_update.update_id,
    )
    await db_session.commit()
    await message.answer(
        "Отправьте текст или нажмите микрофон Telegram и запишите голосовое сообщение.",
        parse_mode=None,
        reply_markup=main_menu_keyboard(),
    )


async def menu_callback(
    callback_query: CallbackQuery,
    db_session: AsyncSession,
) -> None:
    feedback = CallbackFeedback(callback_query)
    if callback_query.data != "nav:menu":
        await feedback.error("Действие недоступно.")
        return
    message = callback_query.message
    if not isinstance(message, Message):
        await feedback.error("Действие недоступно.")
        return
    await feedback.acknowledge("⏳ Открываю меню…")
    await menu_command(message, db_session)
    await feedback.success("Главное меню открыто.", remove_keyboard=True)
