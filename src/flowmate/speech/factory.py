from openai import AsyncOpenAI

from flowmate.core.config import Settings
from flowmate.speech.errors import SpeechConfigurationError
from flowmate.speech.openai_provider import OpenAISpeechToTextProvider
from flowmate.speech.provider import SpeechToTextProvider


def create_speech_provider(settings: Settings) -> SpeechToTextProvider | None:
    if settings.speech_provider is None:
        return None
    if settings.openai_api_key is None or settings.speech_model is None:
        raise SpeechConfigurationError("OpenAI speech configuration is incomplete")

    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=float(settings.speech_timeout_seconds),
    )
    return OpenAISpeechToTextProvider(
        client,
        model=settings.speech_model,
        language=settings.speech_language,
        timeout_seconds=settings.speech_timeout_seconds,
    )
