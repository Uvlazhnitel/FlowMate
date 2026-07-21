import asyncio
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from flowmate.ai.analysis import analysis_to_parse_result, build_analysis_result
from flowmate.ai.errors import AIInvalidResponseError, AITimeoutError
from flowmate.ai.prompt import build_refinement_prompt, build_system_prompt
from flowmate.ai.provider import AIProvider
from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftInputContext,
    DraftParseResult,
    DraftSource,
)

Clock = Callable[[ZoneInfo], datetime]


def current_time(timezone: ZoneInfo) -> datetime:
    return datetime.now(timezone)


class DraftParsingService:
    def __init__(
        self,
        provider: AIProvider,
        *,
        timezone: ZoneInfo,
        active_workspace: str,
        timeout_seconds: int,
        high_confidence_threshold: float,
        clarification_confidence_threshold: float,
        clock: Clock = current_time,
    ) -> None:
        self._provider = provider
        self._timezone = timezone
        self._active_workspace = active_workspace
        self._timeout_seconds = timeout_seconds
        self._high_confidence_threshold = high_confidence_threshold
        self._clarification_confidence_threshold = clarification_confidence_threshold
        self._clock = clock

    async def parse(
        self,
        user_text: str,
        *,
        source: DraftSource,
    ) -> DraftAnalysisResult:
        normalized = user_text.strip()
        if not normalized:
            raise AIInvalidResponseError("note text must not be empty")

        context = self._build_context(source)
        return await self._parse_with_prompt(
            user_text=normalized,
            system_prompt=build_system_prompt(context),
            context=context,
        )

    async def refine(
        self,
        current: DraftAnalysisResult,
        answer_text: str,
        *,
        answer_source: DraftSource,
        question: str,
    ) -> DraftAnalysisResult:
        normalized = answer_text.strip()
        if not normalized:
            raise AIInvalidResponseError("clarification answer must not be empty")

        context = self._build_context(current.context.source)
        system_prompt = build_refinement_prompt(
            context,
            analysis_to_parse_result(current),
            question=question,
            answer_source=answer_source.value,
        )
        return await self._parse_with_prompt(
            user_text=normalized,
            system_prompt=system_prompt,
            context=context,
        )

    def _build_context(self, source: DraftSource) -> DraftInputContext:
        return DraftInputContext(
            current_datetime=self._clock(self._timezone),
            timezone=self._timezone.key,
            active_workspace=self._active_workspace,
            channel="telegram",
            source=source,
        )

    async def _parse_with_prompt(
        self,
        *,
        user_text: str,
        system_prompt: str,
        context: DraftInputContext,
    ) -> DraftAnalysisResult:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                parsed = await self._provider.parse(
                    system_prompt=system_prompt,
                    user_text=user_text,
                )
        except TimeoutError as error:
            raise AITimeoutError("AI draft parsing timed out") from error

        if not isinstance(parsed, DraftParseResult):
            raise AIInvalidResponseError("AI provider returned an invalid draft")
        return build_analysis_result(
            parsed,
            context=context,
            high_threshold=self._high_confidence_threshold,
            clarification_threshold=self._clarification_confidence_threshold,
        )
