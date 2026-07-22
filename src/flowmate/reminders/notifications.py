# ruff: noqa: RUF001
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from flowmate.reminders.enums import ReminderScheduleKind, ReminderType
from flowmate.reminders.service import ReminderDelivery

MAX_NOTIFICATION_LENGTH = 4000


class NotificationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TemporaryNotificationError(NotificationError):
    def __init__(self, code: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(code)
        self.retry_after_seconds = retry_after_seconds


class PermanentNotificationError(NotificationError):
    pass


@dataclass(frozen=True, slots=True)
class ReminderNotification:
    chat_id: int
    text: str
    reply_markup: InlineKeyboardMarkup | None = None


class NotificationService(Protocol):
    async def send(self, notification: ReminderNotification) -> None: ...


def build_reminder_notification(
    delivery: ReminderDelivery,
    *,
    timezone: ZoneInfo | None = None,
    now: datetime | None = None,
) -> ReminderNotification:
    if delivery.telegram_user_id is None:
        raise PermanentNotificationError("missing_telegram_user")
    app_timezone = timezone or ZoneInfo("UTC")
    current = now or datetime.now(UTC)
    title = delivery.work_item_title or "запись без названия"
    subject = (
        f"{', '.join(delivery.person_names)} — {title}"
        if delivery.person_names
        else title
    )
    reference = delivery.reference_at or delivery.scheduled_at
    localized = reference.astimezone(app_timezone)
    local_now = current.astimezone(app_timezone)
    if current > reference:
        date_line = f"Просрочено: {localized:%d.%m.%Y %H:%M}"
    elif localized.date() == local_now.date():
        date_line = f"Запланировано на сегодня, {localized:%H:%M}"
    else:
        date_line = f"Запланировано: {localized:%d.%m.%Y %H:%M}"
    topic_line = f"\nТема: {delivery.topic_name}" if delivery.topic_name else ""
    reminder_id = delivery.reminder_id
    keyboards = {
        ReminderType.DEADLINE: InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Выполнено",
                        callback_data=f"rem:done:{reminder_id}",
                    ),
                    InlineKeyboardButton(
                        text="Отложить",
                        callback_data=f"rem:snooze:{reminder_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Перенести",
                        callback_data=f"rem:reschedule:{reminder_id}",
                    )
                ],
            ]
        ),
        ReminderType.FOLLOW_UP: InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Выполнено",
                        callback_data=f"rem:done:{reminder_id}",
                    ),
                    InlineKeyboardButton(
                        text="Отложить",
                        callback_data=f"rem:snooze:{reminder_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Перенести",
                        callback_data=f"rem:reschedule:{reminder_id}",
                    ),
                    InlineKeyboardButton(
                        text="Ответ получен",
                        callback_data=f"rem:replied:{reminder_id}",
                    ),
                ],
            ]
        ),
        ReminderType.WAITING: InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Получено",
                        callback_data=f"rem:received:{reminder_id}",
                    ),
                    InlineKeyboardButton(
                        text="Follow-up",
                        callback_data=f"rem:followup:{reminder_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Отложить",
                        callback_data=f"rem:snooze:{reminder_id}",
                    )
                ],
            ]
        ),
    }
    deadline_heading = (
        "⏰ Скоро срок"
        if delivery.schedule_kind is ReminderScheduleKind.BEFORE_DEADLINE
        else "⏰ Срок"
    )
    messages = {
        ReminderType.DEADLINE: (
            f"{deadline_heading}\n\n{subject}{topic_line}\n{date_line}"
        ),
        ReminderType.FOLLOW_UP: (f"🔁 Follow-up\n\n{subject}{topic_line}\n{date_line}"),
        ReminderType.WAITING: (f"⏳ Ожидание\n\n{subject}{topic_line}\n{date_line}"),
        ReminderType.MORNING_DIGEST: delivery.message or "Доброе утро.",
        ReminderType.EVENING_DIGEST: delivery.message or "Вечерний обзор.",
        ReminderType.CUSTOM: delivery.message or f"Напоминание: {title}",
    }
    digest_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть сегодня",
                    callback_data=f"dig:today:{reminder_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Перенести незавершённое на завтра",
                    callback_data=f"dig:tomorrow:{reminder_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Разобрать по одному",
                    callback_data=f"dig:review:{reminder_id}",
                )
            ],
        ]
    )
    reply_markup = keyboards.get(delivery.reminder_type)
    if delivery.reminder_type in {
        ReminderType.MORNING_DIGEST,
        ReminderType.EVENING_DIGEST,
    }:
        reply_markup = digest_keyboard
    return ReminderNotification(
        chat_id=delivery.telegram_user_id,
        text=messages[delivery.reminder_type][:MAX_NOTIFICATION_LENGTH],
        reply_markup=reply_markup,
    )


class TelegramNotificationService:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, notification: ReminderNotification) -> None:
        try:
            await self._bot.send_message(
                chat_id=notification.chat_id,
                text=notification.text,
                parse_mode=None,
                reply_markup=notification.reply_markup,
            )
        except TelegramRetryAfter as error:
            raise TemporaryNotificationError(
                "telegram_retry_after",
                error.retry_after,
            ) from error
        except TelegramServerError as error:
            raise TemporaryNotificationError("telegram_server") from error
        except TelegramNetworkError as error:
            raise TemporaryNotificationError("telegram_network") from error
        except TelegramForbiddenError as error:
            raise PermanentNotificationError("telegram_forbidden") from error
        except TelegramNotFound as error:
            raise PermanentNotificationError("telegram_not_found") from error
        except TelegramBadRequest as error:
            raise PermanentNotificationError("telegram_bad_request") from error
        except TelegramUnauthorizedError as error:
            raise PermanentNotificationError("telegram_unauthorized") from error
        except TelegramAPIError as error:
            raise TemporaryNotificationError("telegram_api") from error
