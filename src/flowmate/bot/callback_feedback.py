# ruff: noqa: RUF001
import logging
from dataclasses import dataclass

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

DEFAULT_PENDING_MESSAGE = "⏳ Выполняю…"
UNEXPECTED_ERROR_MESSAGE = "Не удалось выполнить действие. Попробуйте ещё раз."

_STATUS_PREFIXES = ("✅ ", "⚠️ ", "✏️ ")


def with_callback_status(text: str, status: str) -> str:
    """Replace the status added by a previous callback without touching card text."""
    body, separator, tail = text.rpartition("\n\n")
    if separator and tail.startswith(_STATUS_PREFIXES):
        text = body
    return f"{text}\n\n{status}"


@dataclass(slots=True)
class CallbackFeedback:
    callback_query: CallbackQuery
    acknowledged: bool = False

    async def acknowledge(self, text: str = DEFAULT_PENDING_MESSAGE) -> None:
        if self.acknowledged:
            return
        try:
            await self.callback_query.answer(text)
        except TelegramAPIError:
            logger.warning("telegram_callback_ack_failed category=telegram")
        finally:
            self.acknowledged = True

    async def success(self, text: str, *, remove_keyboard: bool = False) -> None:
        await self._show_status(f"✅ {text}", remove_keyboard=remove_keyboard)

    async def error(self, text: str) -> None:
        await self._show_status(f"⚠️ {text}", remove_keyboard=False)

    async def prompt(self, text: str, *, remove_keyboard: bool = False) -> None:
        await self._show_status(
            f"✏️ {text}",
            remove_keyboard=remove_keyboard,
        )

    async def _show_status(self, status: str, *, remove_keyboard: bool) -> None:
        if not self.acknowledged:
            await self.acknowledge()
        message = self.callback_query.message
        if not isinstance(message, Message) or message.text is None:
            await self._fallback(status)
            return
        try:
            await message.edit_text(
                with_callback_status(message.text, status),
                entities=message.entities,
                reply_markup=None if remove_keyboard else message.reply_markup,
            )
        except (TelegramBadRequest, RuntimeError):
            logger.warning("telegram_callback_status_edit_failed category=telegram")
            await self._fallback(status)
        except TelegramAPIError:
            logger.warning("telegram_callback_status_edit_failed category=telegram")
            await self._fallback(status)

    async def _fallback(self, status: str) -> None:
        message = self.callback_query.message
        if not isinstance(message, Message):
            return
        try:
            await message.answer(status, parse_mode=None)
        except (TelegramAPIError, RuntimeError):
            logger.warning("telegram_callback_status_send_failed category=telegram")


async def show_unexpected_callback_error(callback_query: CallbackQuery) -> None:
    feedback = CallbackFeedback(callback_query, acknowledged=True)
    await feedback.error(UNEXPECTED_ERROR_MESSAGE)
