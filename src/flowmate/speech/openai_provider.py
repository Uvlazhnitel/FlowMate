from pathlib import Path

from openai import AsyncOpenAI, OpenAIError

from flowmate.speech.errors import (
    InvalidTranscriptionResponseError,
    SpeechProviderError,
)


class OpenAISpeechToTextProvider:
    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        language: str,
        timeout_seconds: int,
    ) -> None:
        self._client = client
        self._model = model
        self._language = language
        self._timeout_seconds = timeout_seconds

    async def transcribe(self, audio_path: Path) -> str:
        try:
            with audio_path.open("rb") as audio_file:
                response = await self._client.audio.transcriptions.create(
                    file=audio_file,
                    model=self._model,
                    language=self._language,
                    response_format="json",
                    timeout=float(self._timeout_seconds),
                )
        except OpenAIError as error:
            raise SpeechProviderError("speech provider request failed") from error

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise InvalidTranscriptionResponseError(
                "speech provider returned an empty transcription"
            )
        return text.strip()

    async def close(self) -> None:
        await self._client.close()
