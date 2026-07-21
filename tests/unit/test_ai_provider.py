import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from flowmate.ai.errors import (
    AIConfigurationError,
    AIInvalidResponseError,
    AIProviderError,
    AITimeoutError,
)
from flowmate.ai.factory import create_ai_provider
from flowmate.ai.openai_provider import OpenAIAIProvider
from flowmate.ai.prompt import build_system_prompt
from flowmate.ai.schemas import DraftItemType, DraftParseResult, DraftSource
from flowmate.ai.service import DraftParsingService
from flowmate.core.config import Settings
from tests.ai_factories import make_context, make_parse_result


def make_result() -> DraftParseResult:
    return make_parse_result(confidence=0.8)


def make_client(output: object) -> tuple[AsyncOpenAI, AsyncMock, AsyncMock]:
    parse = AsyncMock(return_value=SimpleNamespace(output_parsed=output))
    close = AsyncMock()
    client = SimpleNamespace(
        responses=SimpleNamespace(parse=parse),
        close=close,
    )
    return cast(AsyncOpenAI, client), parse, close


@pytest.mark.asyncio
async def test_openai_provider_uses_structured_responses_without_tools() -> None:
    result = make_result()
    client, parse, close = make_client(result)
    provider = OpenAIAIProvider(client, model="configured-model", timeout_seconds=17)

    parsed = await provider.parse(system_prompt="safe prompt", user_text="user note")
    await provider.close()

    assert parsed is result
    parse.assert_awaited_once_with(
        model="configured-model",
        instructions="safe prompt",
        input="user note",
        text_format=DraftParseResult,
        store=False,
        tools=[],
        timeout=17.0,
    )
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_provider_rejects_missing_or_wrong_parsed_output() -> None:
    outputs: tuple[object, ...] = (None, {"draft_items": []})
    for output in outputs:
        client, _, _ = make_client(output)
        provider = OpenAIAIProvider(client, model="model", timeout_seconds=10)

        with pytest.raises(AIInvalidResponseError):
            await provider.parse(system_prompt="prompt", user_text="note")


@pytest.mark.asyncio
async def test_openai_provider_maps_validation_and_sdk_errors() -> None:
    with pytest.raises(ValidationError) as validation:
        DraftParseResult.model_validate({})

    client, parse, _ = make_client(None)
    provider = OpenAIAIProvider(client, model="model", timeout_seconds=10)
    parse.side_effect = validation.value
    with pytest.raises(AIInvalidResponseError):
        await provider.parse(system_prompt="prompt", user_text="note")

    parse.side_effect = OpenAIError("private provider detail")
    with pytest.raises(AIProviderError, match="provider request failed"):
        await provider.parse(system_prompt="prompt", user_text="private note")


@pytest.mark.asyncio
async def test_openai_provider_does_not_log_sensitive_request_or_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_prompt = "private system prompt"
    private_text = "private user text"
    private_response = "private model response"
    private_key = "sk-private-api-key"
    client, parse, _ = make_client(None)
    provider = OpenAIAIProvider(client, model="model", timeout_seconds=10)
    parse.side_effect = OpenAIError(private_response)

    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(AIProviderError),
    ):
        await provider.parse(system_prompt=private_prompt, user_text=private_text)

    for secret in (private_prompt, private_text, private_response, private_key):
        assert secret not in caplog.text


class SlowProvider:
    async def parse(self, *, system_prompt: str, user_text: str) -> DraftParseResult:
        await asyncio.sleep(1)
        return make_result()

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_draft_service_enforces_overall_timeout() -> None:
    service = DraftParsingService(
        SlowProvider(),
        timezone=ZoneInfo("UTC"),
        active_workspace="personal",
        timeout_seconds=0,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
    )

    with pytest.raises(AITimeoutError):
        await service.parse("note", source=DraftSource.TEXT)


class CapturingProvider:
    def __init__(self) -> None:
        self.system_prompt = ""
        self.user_text = ""

    async def parse(self, *, system_prompt: str, user_text: str) -> DraftParseResult:
        self.system_prompt = system_prompt
        self.user_text = user_text
        return make_result()

    async def close(self) -> None:
        return None


def fixed_clock(timezone: ZoneInfo) -> datetime:
    return datetime(2026, 7, 20, 12, 30, tzinfo=UTC).astimezone(timezone)


@pytest.mark.asyncio
async def test_service_passes_workspace_source_and_time_context() -> None:
    provider = CapturingProvider()
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("Europe/Riga"),
        active_workspace="client-alpha",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )

    result = await service.parse("  mixed Русский text  ", source=DraftSource.VOICE)

    assert provider.user_text == "mixed Русский text"
    assert "Active workspace: client-alpha" in provider.system_prompt
    assert "Input channel: telegram" in provider.system_prompt
    assert "Input source: voice" in provider.system_prompt
    assert "Reference timezone: Europe/Riga" in provider.system_prompt
    assert result.context.source is DraftSource.VOICE
    assert result.context.active_workspace == "client-alpha"


def test_system_prompt_contains_types_and_reference_timezone() -> None:
    prompt = build_system_prompt(make_context())

    for item_type in DraftItemType:
        assert item_type.value in prompt
    assert "2026-07-20T12:30:00+00:00" in prompt
    assert "Reference timezone: UTC" in prompt
    assert "Never create database records" in prompt
    assert '"сначала"' in prompt
    assert '"если"' in prompt
    assert "23:59:59" in prompt


def test_ai_factory_handles_disabled_and_incomplete_configuration() -> None:
    assert create_ai_provider(Settings(_env_file=None)) is None

    with pytest.raises(AIConfigurationError):
        create_ai_provider(Settings(_env_file=None, ai_provider="openai"))


def test_ai_factory_uses_masked_key_and_configured_model() -> None:
    client = MagicMock(spec=AsyncOpenAI)
    with patch("flowmate.ai.factory.AsyncOpenAI", return_value=client) as client_type:
        provider = create_ai_provider(
            Settings(
                _env_file=None,
                ai_provider="openai",
                openai_api_key="private-ai-key",
                ai_model="configured-model",
                ai_timeout_seconds=25,
            )
        )

    assert provider is not None
    client_type.assert_called_once_with(api_key="private-ai-key", timeout=25.0)
