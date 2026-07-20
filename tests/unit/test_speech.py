# ruff: noqa: ASYNC240
import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import AsyncOpenAI

from flowmate.core.config import Settings
from flowmate.speech.errors import (
    AudioTooLargeError,
    InvalidTranscriptionResponseError,
    SpeechConfigurationError,
    SpeechProviderError,
    SpeechTimeoutError,
)
from flowmate.speech.factory import create_speech_provider
from flowmate.speech.openai_provider import OpenAISpeechToTextProvider
from flowmate.speech.service import TranscriptionService
from flowmate.speech.temp_files import TemporaryAudioFileService


class FakeProvider:
    def __init__(
        self,
        result: str = "Распознанный текст",
        *,
        error: Exception | None = None,
        delay: float = 0,
    ) -> None:
        self.result = result
        self.error = error
        self.delay = delay
        self.audio_path: Path | None = None
        self.calls = 0
        self.closed = False

    async def transcribe(self, audio_path: Path) -> str:
        self.calls += 1
        self.audio_path = audio_path
        assert audio_path.exists()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result

    async def close(self) -> None:
        self.closed = True


def make_service(
    provider: FakeProvider,
    *,
    timeout_seconds: int = 1,
    max_file_size_bytes: int = 100,
) -> TranscriptionService:
    return TranscriptionService(
        provider,
        TemporaryAudioFileService(),
        timeout_seconds=timeout_seconds,
        max_file_size_bytes=max_file_size_bytes,
    )


@pytest.mark.asyncio
async def test_transcription_deletes_temporary_file_after_success() -> None:
    provider = FakeProvider()

    async def download(path: Path, timeout_seconds: int) -> None:
        assert timeout_seconds == 1
        path.write_bytes(b"audio")

    result = await make_service(provider).transcribe(
        download,
        reported_file_size=5,
    )

    assert result == "Распознанный текст"
    assert provider.audio_path is not None
    assert not provider.audio_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    [
        FakeProvider(error=SpeechProviderError("provider failed")),
        FakeProvider(result="   "),
    ],
)
async def test_transcription_deletes_temporary_file_after_provider_failure(
    provider: FakeProvider,
) -> None:
    async def download(path: Path, timeout_seconds: int) -> None:
        path.write_bytes(b"audio")

    with pytest.raises((SpeechProviderError, InvalidTranscriptionResponseError)):
        await make_service(provider).transcribe(download, reported_file_size=5)

    assert provider.audio_path is not None
    assert not provider.audio_path.exists()


@pytest.mark.asyncio
async def test_transcription_deletes_temporary_file_after_timeout() -> None:
    provider = FakeProvider(delay=2)

    async def download(path: Path, timeout_seconds: int) -> None:
        path.write_bytes(b"audio")

    with pytest.raises(SpeechTimeoutError):
        await make_service(provider, timeout_seconds=0).transcribe(
            download,
            reported_file_size=5,
        )

    assert provider.audio_path is not None
    assert not provider.audio_path.exists()


@pytest.mark.asyncio
async def test_transcription_deletes_temporary_file_after_download_failure() -> None:
    provider = FakeProvider()
    downloaded_path: Path | None = None

    async def download(path: Path, timeout_seconds: int) -> None:
        nonlocal downloaded_path
        downloaded_path = path
        raise OSError("download failed")

    with pytest.raises(OSError):
        await make_service(provider).transcribe(download, reported_file_size=5)

    assert downloaded_path is not None
    assert not downloaded_path.exists()
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_reported_oversized_audio_is_rejected_before_download() -> None:
    provider = FakeProvider()
    download = AsyncMock()

    with pytest.raises(AudioTooLargeError):
        await make_service(provider).transcribe(
            download,
            reported_file_size=101,
        )

    download.assert_not_awaited()
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_downloaded_oversized_audio_is_deleted_before_provider_call() -> None:
    provider = FakeProvider()
    downloaded_path: Path | None = None

    async def download(path: Path, timeout_seconds: int) -> None:
        nonlocal downloaded_path
        downloaded_path = path
        path.write_bytes(b"x" * 101)

    with pytest.raises(AudioTooLargeError):
        await make_service(provider).transcribe(download, reported_file_size=None)

    assert downloaded_path is not None
    assert not downloaded_path.exists()
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_openai_provider_sends_original_ogg_with_configured_values(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"audio")
    client_mock = MagicMock()
    client_mock.audio.transcriptions.create = AsyncMock(
        return_value=MagicMock(text="  Текст  ")
    )
    client_mock.close = AsyncMock()
    provider = OpenAISpeechToTextProvider(
        cast(AsyncOpenAI, client_mock),
        model="configured-model",
        language="ru",
        timeout_seconds=60,
    )

    result = await provider.transcribe(audio_path)
    await provider.close()

    assert result == "Текст"
    call = client_mock.audio.transcriptions.create.await_args
    assert Path(call.kwargs["file"].name).suffix == ".ogg"
    assert call.kwargs["model"] == "configured-model"
    assert call.kwargs["language"] == "ru"
    assert call.kwargs["response_format"] == "json"
    assert call.kwargs["timeout"] == 60.0
    client_mock.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_provider_rejects_invalid_response(tmp_path: Path) -> None:
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"audio")
    client_mock = MagicMock()
    client_mock.audio.transcriptions.create = AsyncMock(return_value=object())
    provider = OpenAISpeechToTextProvider(
        cast(AsyncOpenAI, client_mock),
        model="configured-model",
        language="ru",
        timeout_seconds=60,
    )

    with pytest.raises(InvalidTranscriptionResponseError):
        await provider.transcribe(audio_path)


@pytest.mark.parametrize(
    "settings",
    [
        Settings(_env_file=None, speech_provider="openai"),
        Settings(
            _env_file=None,
            speech_provider="openai",
            openai_api_key="dummy-key",
        ),
    ],
)
def test_speech_provider_factory_handles_optional_and_incomplete_config(
    settings: Settings,
) -> None:
    assert create_speech_provider(Settings(_env_file=None)) is None

    with pytest.raises(SpeechConfigurationError):
        create_speech_provider(settings)


@pytest.mark.asyncio
async def test_speech_provider_factory_creates_closeable_openai_provider() -> None:
    provider = create_speech_provider(
        Settings(
            _env_file=None,
            speech_provider="openai",
            openai_api_key="dummy-key",
            speech_model="configured-model",
        )
    )

    assert isinstance(provider, OpenAISpeechToTextProvider)
    await provider.close()
