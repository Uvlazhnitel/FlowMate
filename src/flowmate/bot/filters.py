from aiogram.filters import Filter
from aiogram.types import Message


class AllowedUserFilter(Filter):
    def __init__(self, allowed_user_ids: frozenset[int]) -> None:
        self.allowed_user_ids = frozenset(allowed_user_ids)

    async def __call__(self, message: Message) -> bool:
        return (
            message.from_user is not None
            and message.from_user.id in self.allowed_user_ids
        )
