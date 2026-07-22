# ruff: noqa: RUF001
from datetime import UTC, datetime

from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import UserNotificationPreferences
from flowmate.db.users import get_user_by_telegram_id
from flowmate.reminders.digests import cancel_future_digests
from flowmate.reminders.enums import ReminderType
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_or_create_notification_preferences,
    parse_time,
    validate_timezone,
)

SETTINGS_FAILED = "Не удалось сохранить настройки. Попробуйте позже."


def _arguments(message: Message, command: str) -> list[str]:
    text = message.text or ""
    head, _, tail = text.partition(" ")
    if head.split("@", 1)[0].casefold() != command:
        return []
    return tail.split()


def format_preferences(preferences: UserNotificationPreferences) -> str:
    morning = (
        preferences.morning_digest_time.strftime("%H:%M")
        if preferences.morning_digest_enabled
        else "выключен"
    )
    evening = (
        preferences.evening_digest_time.strftime("%H:%M")
        if preferences.evening_digest_enabled
        else "выключен"
    )
    quiet = (
        f"{preferences.quiet_hours_start:%H:%M}–{preferences.quiet_hours_end:%H:%M}"
        if preferences.quiet_hours_enabled
        else "выключены"
    )
    empty = "да" if preferences.send_empty_digests else "нет"
    return (
        "Настройки напоминаний\n"
        f"Часовой пояс: {preferences.timezone}\n"
        f"Утренний обзор: {morning}\n"
        f"Вечерний обзор: {evening}\n"
        f"Тихие часы: {quiet}\n"
        f"Snooze по умолчанию: {preferences.default_snooze_minutes} мин.\n"
        f"Пустые обзоры: {empty}"
    )


async def reminders_settings_command(
    message: Message,
    db_session: AsyncSession,
    notification_defaults: NotificationDefaults,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    try:
        user = await get_user_by_telegram_id(db_session, telegram_user.id)
        if user is None:
            await message.answer("Сначала используйте /start.")
            return
        preferences = await get_or_create_notification_preferences(
            db_session, user.id, notification_defaults
        )
        args = _arguments(message, "/reminders")
        if args:
            operation = args[0].casefold()
            value = args[1] if len(args) > 1 else ""
            now = datetime.now(UTC)
            if operation == "timezone" and value:
                preferences.timezone = validate_timezone(value)
                await cancel_future_digests(
                    db_session, user.id, ReminderType.MORNING_DIGEST, now=now
                )
                await cancel_future_digests(
                    db_session, user.id, ReminderType.EVENING_DIGEST, now=now
                )
            elif operation in {"morning", "evening"} and value:
                enabled_field = f"{operation}_digest_enabled"
                time_field = f"{operation}_digest_time"
                reminder_type = (
                    ReminderType.MORNING_DIGEST
                    if operation == "morning"
                    else ReminderType.EVENING_DIGEST
                )
                if value.casefold() == "off":
                    setattr(preferences, enabled_field, False)
                    await cancel_future_digests(
                        db_session, user.id, reminder_type, now=now
                    )
                else:
                    setattr(preferences, time_field, parse_time(value))
                    setattr(preferences, enabled_field, True)
            elif operation == "snooze" and value:
                minutes = int(value)
                if not 1 <= minutes <= 10_080:
                    raise ValueError("snooze minutes out of range")
                preferences.default_snooze_minutes = minutes
            elif operation == "empty" and value.casefold() in {"on", "off"}:
                preferences.send_empty_digests = value.casefold() == "on"
            else:
                raise ValueError("unsupported reminder setting")
            await db_session.commit()
        await message.answer(format_preferences(preferences), parse_mode=None)
    except ValueError:
        await db_session.rollback()
        await message.answer(
            "Формат: /reminders timezone Europe/Riga, morning 09:00, "
            "evening off, snooze 60 или empty on."
        )
    except SQLAlchemyError:
        await db_session.rollback()
        await message.answer(SETTINGS_FAILED)


async def quiet_command(
    message: Message,
    db_session: AsyncSession,
    notification_defaults: NotificationDefaults,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    try:
        user = await get_user_by_telegram_id(db_session, telegram_user.id)
        if user is None:
            await message.answer("Сначала используйте /start.")
            return
        preferences = await get_or_create_notification_preferences(
            db_session, user.id, notification_defaults
        )
        args = _arguments(message, "/quiet")
        if args:
            operation = args[0].casefold()
            if operation == "on" and len(args) == 1:
                preferences.quiet_hours_enabled = True
            elif operation == "off" and len(args) == 1:
                preferences.quiet_hours_enabled = False
            elif len(args) == 2:
                start = parse_time(args[0])
                end = parse_time(args[1])
                if start == end:
                    raise ValueError("quiet hours must not cover an ambiguous full day")
                preferences.quiet_hours_start = start
                preferences.quiet_hours_end = end
                preferences.quiet_hours_enabled = True
            else:
                raise ValueError("unsupported quiet hours setting")
            await db_session.commit()
        status = (
            f"Тихие часы: {preferences.quiet_hours_start:%H:%M}–"
            f"{preferences.quiet_hours_end:%H:%M}."
            if preferences.quiet_hours_enabled
            else "Тихие часы выключены."
        )
        await message.answer(status)
    except ValueError:
        await db_session.rollback()
        await message.answer("Формат: /quiet on, /quiet off или /quiet 22:00 08:00.")
    except SQLAlchemyError:
        await db_session.rollback()
        await message.answer(SETTINGS_FAILED)


async def snooze_command(message: Message) -> None:
    from flowmate.bot.handlers.reminders import (
        parse_reminder_callback,
        snooze_keyboard,
    )

    replied = message.reply_to_message
    if replied is not None and replied.reply_markup is not None:
        for row in replied.reply_markup.inline_keyboard:
            for button in row:
                parsed = parse_reminder_callback(button.callback_data)
                if parsed is not None:
                    await message.answer(
                        "На сколько отложить напоминание?",
                        reply_markup=snooze_keyboard(parsed[1]),
                    )
                    return
    await message.answer("Ответьте командой /snooze на сообщение с напоминанием.")
