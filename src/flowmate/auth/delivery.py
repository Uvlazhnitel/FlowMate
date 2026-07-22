# ruff: noqa: RUF001
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError


class LoginCodeDeliveryError(Exception):
    """Telegram could not deliver a PWA login code."""


class LoginCodeSender(Protocol):
    async def send_login_code(
        self,
        telegram_user_id: int,
        code: str,
        expires_in_minutes: int,
    ) -> None: ...


class TelegramLoginCodeSender:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_login_code(
        self,
        telegram_user_id: int,
        code: str,
        expires_in_minutes: int,
    ) -> None:
        try:
            await self._bot.send_message(
                chat_id=telegram_user_id,
                text=(
                    "Код входа в FlowMate: "
                    f"{code}\nОн действует {expires_in_minutes} минут."
                ),
                parse_mode=None,
            )
        except TelegramAPIError as error:
            raise LoginCodeDeliveryError from error
