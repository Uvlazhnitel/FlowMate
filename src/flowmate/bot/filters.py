from typing import Any

from aiogram.filters import Filter
from aiogram.types import Message, Update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.drafts import (
    get_active_draft_for_user,
    get_draft_by_processed_update,
    get_draft_by_question_message,
)
from flowmate.db.users import get_user_by_telegram_id
from flowmate.meetings.review import get_latest_review
from flowmate.meetings.service import get_active_meeting
from flowmate.meetings.setup import get_open_setup
from flowmate.task_engine.action_sessions import get_active_action_session


class ActiveWorkItemActionFilter(Filter):
    async def __call__(
        self,
        message: Message,
        db_session: AsyncSession,
    ) -> bool | dict[str, Any]:
        telegram_user = message.from_user
        if telegram_user is None:
            return False
        try:
            user = await get_user_by_telegram_id(db_session, telegram_user.id)
            if user is None:
                return False
            action = await get_active_action_session(db_session, user.id)
        except SQLAlchemyError:
            await db_session.rollback()
            return False
        if action is None:
            return False
        return {
            "active_work_item_action": action,
            "action_user_id": user.id,
        }


class ActiveDraftFilter(Filter):
    async def __call__(
        self,
        message: Message,
        db_session: AsyncSession,
        event_update: Update,
    ) -> bool | dict[str, Any]:
        telegram_user = message.from_user
        if telegram_user is None:
            return False
        try:
            user = await get_user_by_telegram_id(db_session, telegram_user.id)
            if user is None:
                return False
            draft = await get_active_draft_for_user(db_session, user.id)
        except SQLAlchemyError:
            await db_session.rollback()
            return {"draft_database_failed": True}
        if draft is not None:
            return {"active_draft": draft, "draft_user_id": user.id}

        update_id = event_update.update_id
        if update_id > 0:
            try:
                processed = await get_draft_by_processed_update(
                    db_session,
                    user_id=user.id,
                    update_id=update_id,
                )
            except SQLAlchemyError:
                await db_session.rollback()
                return {"draft_database_failed": True}
            if processed is not None:
                return {
                    "processed_draft_update": True,
                    "draft_user_id": user.id,
                }

        replied = message.reply_to_message
        if replied is None:
            return False
        try:
            expired = await get_draft_by_question_message(
                db_session,
                user_id=user.id,
                message_id=replied.message_id,
            )
        except SQLAlchemyError:
            await db_session.rollback()
            return {"draft_database_failed": True}
        if expired is not None:
            return {"expired_draft": expired, "draft_user_id": user.id}
        return False


class MeetingTitleReplyFilter(Filter):
    async def __call__(
        self, message: Message, db_session: AsyncSession
    ) -> bool | dict[str, Any]:
        telegram_user = message.from_user
        replied = message.reply_to_message
        if telegram_user is None or replied is None:
            return False
        try:
            user = await get_user_by_telegram_id(db_session, telegram_user.id)
            if user is None:
                return False
            setup = await get_open_setup(db_session, user.id)
        except SQLAlchemyError:
            await db_session.rollback()
            return False
        if (
            setup is None
            or setup.step != "title"
            or setup.prompt_message_id != replied.message_id
        ):
            return False
        return {"meeting_setup": setup, "meeting_user_id": user.id}


class ActiveMeetingCaptureFilter(Filter):
    async def __call__(
        self, message: Message, db_session: AsyncSession
    ) -> bool | dict[str, Any]:
        telegram_user = message.from_user
        if telegram_user is None:
            return False
        try:
            user = await get_user_by_telegram_id(db_session, telegram_user.id)
            if user is None:
                return False
            meeting = await get_active_meeting(db_session, user.id)
        except SQLAlchemyError:
            await db_session.rollback()
            return False
        if meeting is None:
            return False
        return {"capture_user_id": user.id, "active_meeting": meeting}


class MeetingReviewReplyFilter(Filter):
    async def __call__(
        self, message: Message, db_session: AsyncSession
    ) -> bool | dict[str, Any]:
        telegram_user = message.from_user
        if telegram_user is None:
            return False
        try:
            user = await get_user_by_telegram_id(db_session, telegram_user.id)
            review = (
                await get_latest_review(db_session, user.id)
                if user is not None
                else None
            )
        except SQLAlchemyError:
            await db_session.rollback()
            return False
        if review is None or review.current_item_id is None:
            return False
        replied = message.reply_to_message
        if (
            replied is not None
            and review.current_question_message_id is not None
            and replied.message_id != review.current_question_message_id
        ):
            return False
        return {"active_meeting_review": review}
