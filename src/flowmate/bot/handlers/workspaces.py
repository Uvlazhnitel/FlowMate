from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.callback_feedback import CallbackFeedback
from flowmate.bot.menu import answer_with_main_menu
from flowmate.db.users import get_user_by_telegram_id
from flowmate.workspace_service import (
    WorkspaceSwitchBlockedError,
    switch_workspace,
)
from flowmate.workspaces import WORKSPACE_LABELS, Workspace

WORKSPACE_DISPLAY_ORDER = (Workspace.WORK, Workspace.PERSONAL)


def workspace_keyboard(active: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        f"• {WORKSPACE_LABELS[workspace.value]}"
                        if workspace.value == active
                        else WORKSPACE_LABELS[workspace.value]
                    ),
                    callback_data=f"ws:{workspace.value}",
                )
                for workspace in WORKSPACE_DISPLAY_ORDER
            ]
        ]
    )


async def workspace_command(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await message.answer("Сначала используйте /start.")
        return
    await message.answer(
        f"Текущее пространство: {WORKSPACE_LABELS[user.active_workspace]}.",
        reply_markup=workspace_keyboard(user.active_workspace),
    )


async def workspace_toggle(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await answer_with_main_menu(message, "Сначала используйте /start.")
        return
    target = (
        Workspace.PERSONAL
        if user.active_workspace == Workspace.WORK.value
        else Workspace.WORK
    )
    try:
        user = await switch_workspace(db_session, user.id, target)
    except WorkspaceSwitchBlockedError as error:
        await db_session.rollback()
        await answer_with_main_menu(message, error.blocker.message)
        return
    except ValueError:
        await db_session.rollback()
        await answer_with_main_menu(message, "Пространство недоступно.")
        return
    await db_session.commit()
    await answer_with_main_menu(
        message,
        f"✅ Включено пространство: {WORKSPACE_LABELS[user.active_workspace]}.",
    )


async def workspace_callback(
    callback_query: CallbackQuery,
    db_session: AsyncSession,
) -> None:
    data = callback_query.data or ""
    if not data.startswith("ws:"):
        return
    feedback = CallbackFeedback(callback_query)
    await feedback.acknowledge("⏳ Переключаю…")
    telegram_user = callback_query.from_user
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await feedback.error("Сначала используйте /start.")
        return
    try:
        workspace = Workspace(data.removeprefix("ws:"))
        user = await switch_workspace(db_session, user.id, workspace)
    except WorkspaceSwitchBlockedError as error:
        await feedback.error(error.blocker.message)
        return
    except ValueError:
        await feedback.error("Пространство недоступно.")
        return
    await db_session.commit()
    if isinstance(callback_query.message, Message):
        await callback_query.message.edit_text(
            f"✅ Текущее пространство: {WORKSPACE_LABELS[user.active_workspace]}.",
            reply_markup=workspace_keyboard(user.active_workspace),
        )
