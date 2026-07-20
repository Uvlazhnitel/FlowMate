from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from flowmate.bot.filters import AllowedUserFilter


def create_router(allowed_user_ids: frozenset[int]) -> Router:
    router = Router(name="flowmate")
    router.message.filter(AllowedUserFilter(allowed_user_ids))

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        await message.answer("FlowMate работает.")

    @router.message(Command("health"))
    async def health(message: Message) -> None:
        await message.answer("ok")

    return router
