from typing import TypeVar

from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from flowmate.ai.errors import AIInvalidResponseError, AIProviderError
from flowmate.ai.schemas import (
    DraftParseResult,
    MeetingReviewParseResult,
    SnoozeTimeParseResult,
    TelegramTextParseResult,
)

StructuredResult = TypeVar(
    "StructuredResult",
    DraftParseResult,
    TelegramTextParseResult,
    SnoozeTimeParseResult,
    MeetingReviewParseResult,
)


class OpenAIAIProvider:
    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        timeout_seconds: int,
    ) -> None:
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def parse(self, *, system_prompt: str, user_text: str) -> DraftParseResult:
        return await self._parse_response(
            system_prompt=system_prompt,
            user_text=user_text,
            response_type=DraftParseResult,
        )

    async def parse_text(
        self,
        *,
        system_prompt: str,
        user_text: str,
    ) -> TelegramTextParseResult:
        return await self._parse_response(
            system_prompt=system_prompt,
            user_text=user_text,
            response_type=TelegramTextParseResult,
        )

    async def parse_snooze_time(
        self,
        *,
        system_prompt: str,
        user_text: str,
    ) -> SnoozeTimeParseResult:
        return await self._parse_response(
            system_prompt=system_prompt,
            user_text=user_text,
            response_type=SnoozeTimeParseResult,
        )

    async def parse_meeting_review(
        self, *, system_prompt: str, user_text: str
    ) -> MeetingReviewParseResult:
        return await self._parse_response(
            system_prompt=system_prompt,
            user_text=user_text,
            response_type=MeetingReviewParseResult,
        )

    async def _parse_response(
        self,
        *,
        system_prompt: str,
        user_text: str,
        response_type: type[StructuredResult],
    ) -> StructuredResult:
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=system_prompt,
                input=user_text,
                text_format=response_type,
                store=False,
                tools=[],
                timeout=float(self._timeout_seconds),
            )
        except ValidationError as error:
            raise AIInvalidResponseError("AI response failed validation") from error
        except OpenAIError as error:
            raise AIProviderError("AI provider request failed") from error

        parsed = response.output_parsed
        if not isinstance(parsed, response_type):
            raise AIInvalidResponseError("AI provider returned no structured result")
        return parsed

    async def close(self) -> None:
        await self._client.close()
