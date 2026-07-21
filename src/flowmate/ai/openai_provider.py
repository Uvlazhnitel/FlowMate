from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from flowmate.ai.errors import AIInvalidResponseError, AIProviderError
from flowmate.ai.schemas import DraftParseResult


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
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=system_prompt,
                input=user_text,
                text_format=DraftParseResult,
                store=False,
                tools=[],
                timeout=float(self._timeout_seconds),
            )
        except ValidationError as error:
            raise AIInvalidResponseError("AI response failed validation") from error
        except OpenAIError as error:
            raise AIProviderError("AI provider request failed") from error

        parsed = response.output_parsed
        if not isinstance(parsed, DraftParseResult):
            raise AIInvalidResponseError("AI provider returned no structured draft")
        return parsed

    async def close(self) -> None:
        await self._client.close()
