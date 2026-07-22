from typing import Protocol, runtime_checkable

from flowmate.ai.schemas import (
    DraftParseResult,
    SnoozeTimeParseResult,
    TelegramTextParseResult,
)


class AIProvider(Protocol):
    async def parse(self, *, system_prompt: str, user_text: str) -> DraftParseResult:
        """Parse user text into a validated draft without side effects."""
        ...

    async def close(self) -> None:
        """Release provider resources."""
        ...


@runtime_checkable
class TextRoutingProvider(Protocol):
    async def parse_text(
        self,
        *,
        system_prompt: str,
        user_text: str,
    ) -> TelegramTextParseResult:
        """Route Telegram text to a new draft or a management intent."""
        ...


@runtime_checkable
class SnoozeTimeProvider(Protocol):
    async def parse_snooze_time(
        self,
        *,
        system_prompt: str,
        user_text: str,
    ) -> SnoozeTimeParseResult:
        """Parse one future snooze time without side effects."""
        ...
