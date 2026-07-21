from typing import Protocol

from flowmate.ai.schemas import DraftParseResult


class AIProvider(Protocol):
    async def parse(self, *, system_prompt: str, user_text: str) -> DraftParseResult:
        """Parse user text into a validated draft without side effects."""
        ...

    async def close(self) -> None:
        """Release provider resources."""
        ...
