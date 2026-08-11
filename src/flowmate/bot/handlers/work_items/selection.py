import json
import logging
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import CallbackQuery, Message, Update
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import ManagementIntent
from flowmate.bot.callback_data import (
    decode_revision as decode_revision,
)
from flowmate.bot.callback_data import (
    encode_revision as encode_revision,
)
from flowmate.bot.callback_feedback import CallbackFeedback
from flowmate.db.users import get_user_by_telegram_id
from flowmate.reminders.preferences import (
    NotificationDefaults,
)
from flowmate.reminders.sync import ReminderPolicy
from flowmate.task_engine.action_sessions import (
    finish_action_session,
    get_action_session_for_user,
)
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.rescheduling import (
    ReschedulingService,
)
from flowmate.task_engine.service import (
    get_work_item,
)

from .management import apply_management_intent

logger = logging.getLogger(__name__)


async def work_item_selection_callback(
    callback_query: CallbackQuery,
    event_update: Update,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
    work_item_action_ttl_minutes: int,
    reminder_policy: ReminderPolicy | None = None,
    notification_defaults: NotificationDefaults | None = None,
    rescheduling_service: ReschedulingService | None = None,
) -> None:
    feedback = CallbackFeedback(callback_query)
    message = callback_query.message
    parts = callback_query.data.split(":") if callback_query.data else []
    if len(parts) != 3 or parts[0] != "wis" or not isinstance(message, Message):
        await feedback.error("Выбор недоступен.")
        return
    await feedback.acknowledge()
    try:
        action_session_id = UUID(parts[1])
    except ValueError:
        await feedback.error("Выбор недоступен.")
        return
    user = await get_user_by_telegram_id(db_session, callback_query.from_user.id)
    if user is None:
        await feedback.error("Выбор недоступен.")
        return
    action_session = await get_action_session_for_user(
        db_session,
        user.id,
        action_session_id,
        for_update=True,
    )
    if action_session is None or action_session.action != WorkItemAction.SELECT_RECORD:
        await db_session.rollback()
        await feedback.error("Срок выбора истёк.")
        return
    if parts[2] == "x":
        await finish_action_session(db_session, action_session, status="cancelled")
        await db_session.commit()
        await message.edit_text("✅ Выбор отменён.", parse_mode=None)
        return
    try:
        index = int(parts[2])
    except ValueError:
        await db_session.rollback()
        await feedback.error("Выбор недоступен.")
        return
    candidate_ids = action_session.context.get("candidate_ids")
    intent_payload = action_session.context.get("intent")
    if not isinstance(candidate_ids, list) or not isinstance(intent_payload, dict):
        await db_session.rollback()
        await feedback.error("Выбор недоступен.")
        return
    try:
        item_id = UUID(str(candidate_ids[index]))
        intent = ManagementIntent.model_validate_json(json.dumps(intent_payload))
    except (IndexError, ValueError):
        await db_session.rollback()
        await feedback.error("Выбор недоступен.")
        return
    item = await get_work_item(db_session, user.id, item_id)
    if item is None:
        await db_session.rollback()
        await feedback.error("Запись не найдена.")
        return
    await finish_action_session(db_session, action_session)
    await db_session.flush()
    await apply_management_intent(
        message,
        event_update,
        db_session,
        user_id=user.id,
        telegram_user_id=callback_query.from_user.id,
        item=item,
        intent=intent,
        action_ttl_minutes=work_item_action_ttl_minutes,
        app_timezone=app_timezone,
        reminder_policy=reminder_policy,
        notification_defaults=notification_defaults,
        rescheduling_service=rescheduling_service,
    )
    await feedback.success("Запись выбрана.", remove_keyboard=True)
