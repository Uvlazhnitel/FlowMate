# ruff: noqa: RUF001
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
from flowmate.ai.schemas import (
    DependencyCandidate,
    DependencyRelation,
    DraftAnalysisResult,
    DraftItemType,
    DraftParseResult,
    DraftSource,
    ItemizationBasis,
    ManagementAction,
    ManagementIntent,
    SearchIntent,
    SearchWorkItemType,
    TelegramTextParseResult,
    TemporalCandidate,
    TemporalStatus,
)
from flowmate.ai.service import DraftParsingService
from flowmate.core.config import Settings
from tests.ai_factories import (
    make_context,
    make_draft_item,
    make_parse_result,
    make_temporal_candidate,
)


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
async def test_openai_provider_uses_strict_text_routing_schema() -> None:
    intent = ManagementIntent(
        action=ManagementAction.COMPLETE,
        target_type=DraftItemType.FOLLOW_UP,
        record_query="Антон",
        contextual_reference=False,
        person_candidate="Антон",
        topic_candidate=None,
        note_text=None,
        temporal_candidate=None,
        missing_fields=[],
        ambiguities=[],
        confidence=0.94,
    )
    result = TelegramTextParseResult(
        mode="management",
        draft=None,
        management=intent,
    )
    client, parse, _ = make_client(result)
    provider = OpenAIAIProvider(client, model="configured-model", timeout_seconds=17)

    parsed = await provider.parse_text(
        system_prompt="routing prompt",
        user_text="закрой follow-up с Антоном",
    )

    assert parsed is result
    parse.assert_awaited_once_with(
        model="configured-model",
        instructions="routing prompt",
        input="закрой follow-up с Антоном",
        text_format=TelegramTextParseResult,
        store=False,
        tools=[],
        timeout=17.0,
    )


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
    with pytest.raises(AIInvalidResponseError) as invalid:
        await provider.parse(system_prompt="prompt", user_text="note")
    assert invalid.value.safe_code == "ai_response_validation"

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


class RoutingProvider(CapturingProvider):
    def __init__(self, result: TelegramTextParseResult) -> None:
        super().__init__()
        self.result = result

    async def parse_text(
        self,
        *,
        system_prompt: str,
        user_text: str,
    ) -> TelegramTextParseResult:
        self.system_prompt = system_prompt
        self.user_text = user_text
        return self.result


class SequentialProvider(CapturingProvider):
    def __init__(self, results: list[DraftParseResult]) -> None:
        super().__init__()
        self.results = results
        self.prompts: list[str] = []

    async def parse(self, *, system_prompt: str, user_text: str) -> DraftParseResult:
        self.prompts.append(system_prompt)
        self.user_text = user_text
        return self.results.pop(0)


class RepairingRoutingProvider(SequentialProvider):
    def __init__(
        self,
        routing_result: TelegramTextParseResult,
        repair_results: list[DraftParseResult],
    ) -> None:
        super().__init__(repair_results)
        self.routing_result = routing_result

    async def parse_text(
        self,
        *,
        system_prompt: str,
        user_text: str,
    ) -> TelegramTextParseResult:
        self.system_prompt = system_prompt
        self.user_text = user_text
        return self.routing_result


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


@pytest.mark.asyncio
async def test_service_uses_capture_reference_datetime_when_supplied() -> None:
    provider = CapturingProvider()
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("Europe/Riga"),
        active_workspace="work",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )
    captured_at = datetime.fromisoformat("2026-08-13T13:26:37+00:00")

    result = await service.parse(
        "Завтра сделать задачу",
        source=DraftSource.TEXT,
        reference_datetime=captured_at,
    )

    assert "2026-08-13T16:26:37+03:00" in provider.system_prompt
    assert result.context.current_datetime == captured_at.astimezone(
        ZoneInfo("Europe/Riga")
    )


@pytest.mark.asyncio
async def test_service_accepts_production_sequence_without_consolidated_item() -> None:
    tomorrow = make_temporal_candidate(
        original_phrase="Завтра",
        normalized_value=datetime.fromisoformat("2026-08-14T00:00:00+03:00"),
        time_was_explicit=False,
    )
    parsed = make_parse_result(
        [
            make_draft_item(
                title="Добавить людей в OrgChart",
                due_date_candidate=tomorrow,
                confidence=0.94,
            ),
            make_draft_item(
                title="Сделать CDP refresher",
                dependencies=[
                    DependencyCandidate(
                        relation=DependencyRelation.AFTER,
                        original_phrase="затем",
                        target_item_number=1,
                        condition=None,
                    )
                ],
                confidence=0.93,
            ),
            make_draft_item(
                title="Добавить людей в forecast",
                dependencies=[
                    DependencyCandidate(
                        relation=DependencyRelation.AFTER,
                        original_phrase="затем",
                        target_item_number=2,
                        condition=None,
                    )
                ],
                confidence=0.92,
            ),
        ],
        itemization_basis=ItemizationBasis.INDEPENDENT_OUTCOMES,
        itemization_confidence=0.90,
        consolidated_item=None,
        confidence=0.92,
    )
    provider = RoutingProvider(TelegramTextParseResult(mode="new_draft", draft=parsed))
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("Europe/Riga"),
        active_workspace="work",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )

    result = await service.parse_text(
        "Завтра добавить людей в OrgChart, затем сделать CDP refresher, "
        "затем добавить людей в форкаст"
    )

    assert isinstance(result, DraftAnalysisResult)
    assert [item.item.title for item in result.items] == [
        "Добавить людей в OrgChart",
        "Сделать CDP refresher",
        "Добавить людей в forecast",
    ]
    assert [
        item.item.due_date_candidate.normalized_value
        if item.item.due_date_candidate is not None
        else None
        for item in result.items
    ] == [datetime.fromisoformat("2026-08-14T23:59:59+03:00")] * 3


@pytest.mark.asyncio
async def test_service_keeps_then_steps_for_one_deliverable_together() -> None:
    provider = RoutingProvider(
        TelegramTextParseResult(
            mode="new_draft",
            draft=make_parse_result(
                [make_draft_item(title="Подготовить и отправить отчёт")]
            ),
        )
    )
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("UTC"),
        active_workspace="work",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )

    result = await service.parse_text(
        "Подготовить данные, затем отправить тот же отчёт"
    )

    assert isinstance(result, DraftAnalysisResult)
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_service_repairs_missing_low_confidence_consolidation_once() -> None:
    split = make_parse_result(
        [make_draft_item(title="Шаг один"), make_draft_item(title="Шаг два")],
        itemization_basis=ItemizationBasis.UNCERTAIN,
        itemization_confidence=0.70,
        consolidated_item=None,
    )
    repaired = make_parse_result([make_draft_item(title="Выполнить общий результат")])
    provider = SequentialProvider([split, repaired])
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("UTC"),
        active_workspace="work",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )

    result = await service.parse(
        "Подготовить данные и отправить отчёт",
        source=DraftSource.TEXT,
    )

    assert [item.item.title for item in result.items] == ["Выполнить общий результат"]
    assert len(provider.prompts) == 2
    assert "only consolidation repair attempt" in provider.prompts[1]


@pytest.mark.asyncio
async def test_service_rejects_invalid_consolidation_repair() -> None:
    split = make_parse_result(
        [make_draft_item(title="Шаг один"), make_draft_item(title="Шаг два")],
        itemization_basis=ItemizationBasis.UNCERTAIN,
        itemization_confidence=0.70,
        consolidated_item=None,
    )
    invalid_repair = make_parse_result(
        [make_draft_item(title="Первое"), make_draft_item(title="Второе")]
    )
    provider = SequentialProvider([split, invalid_repair])
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("UTC"),
        active_workspace="work",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )

    with pytest.raises(AIInvalidResponseError) as error:
        await service.parse(
            "Подготовить данные и отправить отчёт",
            source=DraftSource.TEXT,
        )

    assert error.value.safe_code == "ai_consolidation_invalid"
    assert not provider.results


@pytest.mark.asyncio
async def test_service_repairs_explicit_voice_segments_once() -> None:
    repaired = make_parse_result(
        [
            make_draft_item(title="Первая"),
            make_draft_item(title="Вторая"),
            make_draft_item(title="Третья"),
            make_draft_item(title="Четвёртая"),
        ]
    )
    provider = SequentialProvider([make_result(), repaired])
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("UTC"),
        active_workspace="work",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )
    text = (
        "Сделать первое. Еще одна задача. Сделать второе. Другая задача. "
        "Сделать третье. Следующая задача. Сделать четвертое."
    )

    result = await service.parse(text, source=DraftSource.VOICE)

    assert [item.item.title for item in result.items] == [
        "Первая",
        "Вторая",
        "Третья",
        "Четвёртая",
    ]
    assert len(provider.prompts) == 2
    assert "only repair attempt" in provider.prompts[1]
    assert "exactly 4 items" in provider.prompts[1]


@pytest.mark.asyncio
async def test_service_rejects_second_wrong_explicit_segment_count() -> None:
    provider = SequentialProvider(
        [
            make_result(),
            make_parse_result(
                [
                    make_draft_item(title="Первая"),
                    make_draft_item(title="Вторая"),
                    make_draft_item(title="Третья"),
                ]
            ),
        ]
    )
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("UTC"),
        active_workspace="work",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )
    text = (
        "Сделать первое. Еще одна задача. Сделать второе. Другая задача. "
        "Сделать третье. Следующая задача. Сделать четвертое."
    )

    with pytest.raises(AIInvalidResponseError, match="explicit boundaries"):
        await service.parse(text, source=DraftSource.VOICE)

    assert not provider.results


@pytest.mark.asyncio
async def test_text_routing_repairs_new_draft_with_explicit_segments() -> None:
    initial = make_result()
    repaired = make_parse_result(
        [make_draft_item(title="Первая"), make_draft_item(title="Вторая")]
    )
    provider = RepairingRoutingProvider(
        TelegramTextParseResult(mode="new_draft", draft=initial),
        [repaired],
    )
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("UTC"),
        active_workspace="work",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )

    result = await service.parse_text("Сделать первое. Другая задача. Сделать второе.")

    assert isinstance(result, DraftAnalysisResult)
    assert [item.item.title for item in result.items] == ["Первая", "Вторая"]
    assert "exactly 2 draft items" in provider.system_prompt


@pytest.mark.asyncio
async def test_service_routes_management_without_creating_a_draft() -> None:
    intent = ManagementIntent(
        action=ManagementAction.WAITING_RECEIVED,
        target_type=DraftItemType.WAITING,
        record_query="ответ Антона",
        contextual_reference=False,
        person_candidate="Антон",
        topic_candidate=None,
        note_text=None,
        temporal_candidate=None,
        missing_fields=[],
        ambiguities=[],
        confidence=0.9,
    )
    provider = RoutingProvider(
        TelegramTextParseResult(
            mode="management",
            draft=None,
            management=intent,
        )
    )
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("Europe/Riga"),
        active_workspace="personal",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )

    result = await service.parse_text(" Антон ответил ")

    assert result is intent
    assert provider.user_text == "Антон ответил"
    assert "new_draft" in provider.system_prompt
    assert "management" in provider.system_prompt


@pytest.mark.asyncio
async def test_service_corrects_future_contextual_reopen_to_reschedule() -> None:
    intent = ManagementIntent(
        action=ManagementAction.REOPEN,
        target_type=DraftItemType.TASK,
        record_query="задача",
        contextual_reference=True,
        person_candidate=None,
        topic_candidate=None,
        note_text=None,
        temporal_candidate=TemporalCandidate(
            original_phrase="завтра",
            normalized_value=datetime(2026, 7, 21, 23, 59, 59, tzinfo=UTC),
            status=TemporalStatus.RESOLVED,
            explanation=None,
            time_was_explicit=False,
        ),
        missing_fields=[],
        ambiguities=[],
        confidence=0.9,
    )
    provider = RoutingProvider(
        TelegramTextParseResult(mode="management", management=intent)
    )
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("UTC"),
        active_workspace="personal",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )

    result = await service.parse_text("нужно завтра выполнить эту задачу")

    assert isinstance(result, ManagementIntent)
    assert result.action is ManagementAction.RESCHEDULE


@pytest.mark.asyncio
async def test_service_routes_conversational_search_to_strict_filters() -> None:
    intent = SearchIntent(
        text_query=None,
        person_query=None,
        topic_query="Testing",
        item_types=[SearchWorkItemType.FOLLOW_UP],
        statuses=[],
        include_all_statuses=False,
        due_from=None,
        due_to=None,
        overdue=True,
        stale_contacts=False,
        ambiguities=[],
        confidence=0.96,
    )
    provider = RoutingProvider(TelegramTextParseResult(mode="search", search=intent))
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("Europe/Riga"),
        active_workspace="personal",
        timeout_seconds=10,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=fixed_clock,
    )

    result = await service.parse_text("Какие follow-up просрочены по Testing?")

    assert result is intent
    assert provider.user_text == "Какие follow-up просрочены по Testing?"
    assert "Do not invent results" in " ".join(provider.system_prompt.split())


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
