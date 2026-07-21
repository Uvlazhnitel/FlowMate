from openai import AsyncOpenAI

from flowmate.ai.errors import AIConfigurationError
from flowmate.ai.openai_provider import OpenAIAIProvider
from flowmate.ai.provider import AIProvider
from flowmate.core.config import Settings


def create_ai_provider(settings: Settings) -> AIProvider | None:
    if settings.ai_provider is None:
        return None
    if settings.openai_api_key is None or settings.ai_model is None:
        raise AIConfigurationError("OpenAI AI configuration is incomplete")

    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=float(settings.ai_timeout_seconds),
    )
    return OpenAIAIProvider(
        client,
        model=settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
    )
