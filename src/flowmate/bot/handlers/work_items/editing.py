import logging
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import (
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.callback_data import (
    decode_revision as decode_revision,
)
from flowmate.bot.callback_data import (
    encode_revision as encode_revision,
)
from flowmate.bot.callback_feedback import CallbackFeedback
from flowmate.db.models import WorkItem
from flowmate.task_engine.action_sessions import (
    create_action_session,
)
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.management import (
    StaleWorkItemError,
    update_work_item_content,
    work_item_revision,
)

from .cards import (
    item_action_data,
    refresh_work_item_card,
    work_item_callback_data,
)

logger = logging.getLogger(__name__)

EDIT_ACTIONS = {"e", "et", "ed", "er", "ec"}


def edit_options_keyboard(item: WorkItem) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Название", callback_data=item_action_data("et", item)
            ),
            InlineKeyboardButton(
                text="Описание", callback_data=item_action_data("ed", item)
            ),
        ],
        [InlineKeyboardButton(text="Дата", callback_data=item_action_data("er", item))],
    ]
    if item.description:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Очистить описание",
                    callback_data=item_action_data("ec", item),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=work_item_callback_data(
                    "b", item.id, workspace=item.workspace
                ),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def start_input_session(
    message: Message,
    session: AsyncSession,
    *,
    user_id: UUID,
    item_id: UUID,
    action: WorkItemAction,
    prompt: str,
    ttl_minutes: int,
    telegram_update_id: int,
    context: dict[str, object] | None = None,
) -> None:
    action_session = await create_action_session(
        session,
        user_id=user_id,
        action=action,
        ttl_minutes=ttl_minutes,
        work_item_id=item_id,
        context=context,
        telegram_update_id=telegram_update_id,
    )
    if action_session.prompt_message_id is not None:
        await session.rollback()
        await message.answer("Запрос уже обработан.")
        return
    sent = await message.answer(prompt, reply_markup=ForceReply(selective=True))
    action_session.prompt_message_id = sent.message_id
    await session.commit()


async def handle_edit_callback(
    message: Message,
    event_update: Update,
    session: AsyncSession,
    feedback: CallbackFeedback,
    *,
    action: str,
    user_id: UUID,
    item: WorkItem,
    expected_revision: int,
    action_ttl_minutes: int,
    app_timezone: ZoneInfo,
) -> None:
    if work_item_revision(item.updated_at) != expected_revision:
        raise StaleWorkItemError("work item card is stale")
    if action == "e":
        await message.edit_reply_markup(reply_markup=edit_options_keyboard(item))
        return
    if action == "ec":
        result = await update_work_item_content(
            session,
            user_id,
            item.id,
            event_update.update_id,
            description=None,
            update_description=True,
            expected_revision=expected_revision,
        )
        await session.commit()
        await refresh_work_item_card(
            message,
            session,
            user_id,
            result.work_item,
            app_timezone,
            notice="✅ Описание очищено.",
        )
        return
    session_action = (
        WorkItemAction.RESCHEDULE if action == "er" else WorkItemAction.EDIT_FIELD
    )
    prompt = {
        "et": "Отправьте новое название текстом или голосом.",
        "ed": "Отправьте новое описание текстом или голосом.",
        "er": (
            "Когда выполнить задачу? Можно ответить свободной фразой "
            "текстом или голосом."
        ),
    }[action]
    context: dict[str, object] = {
        "origin_chat_id": message.chat.id,
        "origin_message_id": message.message_id,
        "work_item_revision": work_item_revision(item.updated_at),
    }
    if action != "er":
        context["edit_field"] = "title" if action == "et" else "description"
    await start_input_session(
        message,
        session,
        user_id=user_id,
        item_id=item.id,
        action=session_action,
        prompt=f"{prompt} Для отмены используйте /cancel.",
        ttl_minutes=action_ttl_minutes,
        telegram_update_id=event_update.update_id,
        context=context,
    )
    await feedback.prompt("Жду ответ.", remove_keyboard=True)
