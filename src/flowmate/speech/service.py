import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from flowmate.speech.errors import (
    AudioTooLargeError,
    InvalidTranscriptionResponseError,
    SpeechTimeoutError,
)
from flowmate.speech.provider import SpeechToTextProvider
from flowmate.speech.temp_files import TemporaryAudioFileService

AudioDownloader = Callable[[Path, int], Awaitable[None]]


class TranscriptionService:
    def __init__(
        self,
        provider: SpeechToTextProvider,
        temporary_files: TemporaryAudioFileService,
        *,
        timeout_seconds: int,
        max_file_size_bytes: int,
    ) -> None:
        self._provider = provider
        self._temporary_files = temporary_files
        self.timeout_seconds = timeout_seconds
        self.max_file_size_bytes = max_file_size_bytes

    def is_too_large(self, file_size: int | None) -> bool:
        return file_size is not None and file_size > self.max_file_size_bytes

    async def transcribe(
        self,
        downloader: AudioDownloader,
        *,
        reported_file_size: int | None,
    ) -> str:
        if self.is_too_large(reported_file_size):
            raise AudioTooLargeError("reported audio size exceeds the limit")

        try:
            async with self._temporary_files.create() as audio_path:
                async with asyncio.timeout(self.timeout_seconds):
                    await downloader(audio_path, self.timeout_seconds)
                    if audio_path.stat().st_size > self.max_file_size_bytes:
                        raise AudioTooLargeError(
                            "downloaded audio size exceeds the limit"
                        )
                    text = await self._provider.transcribe(audio_path)
        except TimeoutError as error:
            raise SpeechTimeoutError("voice transcription timed out") from error

        normalized = text.strip()
        if not normalized:
            raise InvalidTranscriptionResponseError("transcription is empty")
        return normalized
