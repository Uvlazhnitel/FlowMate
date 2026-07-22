import asyncio
import re
from collections.abc import Callable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from flowmate.ai.analysis import analysis_to_parse_result, build_analysis_result
from flowmate.ai.errors import AIInvalidResponseError, AITimeoutError
from flowmate.ai.prompt import (
    build_refinement_prompt,
    build_system_prompt,
    build_text_routing_prompt,
)
from flowmate.ai.provider import AIProvider, TextRoutingProvider
from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftInputContext,
    DraftItem,
    DraftParseResult,
    DraftSource,
    ManagementIntent,
    MeetingDraftContext,
    SearchIntent,
    TelegramTextParseResult,
    TemporalCandidate,
    TemporalStatus,
)

Clock = Callable[[ZoneInfo], datetime]

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
EXACT_LOCAL_DATE = re.compile(
    r"(?<!\d)(?P<day>[0-3]?\d)(?:[.]\s*(?P<number_month>0?[1-9]|1[0-2])"
    r"|\s+(?P<word_month>" + "|".join(MONTHS) + r"))"
    r"(?:[.]?\s*(?P<year>20\d{2}))?(?!\d)",
    re.IGNORECASE,
)
TEMPORAL_AMBIGUITY_MARKERS = (
    "дат",
    "врем",
    "когда",
    "напомин",
    "срок",
    *MONTHS,
)


def current_time(timezone: ZoneInfo) -> datetime:
    return datetime.now(timezone)


def parse_exact_local_date(
    value: str, context: DraftInputContext
) -> TemporalCandidate | None:
    match = EXACT_LOCAL_DATE.search(value.strip())
    if match is None:
        return None
    month = (
        int(match.group("number_month"))
        if match.group("number_month")
        else MONTHS[match.group("word_month").casefold()]
    )
    current = context.current_datetime.astimezone(ZoneInfo(context.timezone))
    year = int(match.group("year")) if match.group("year") else current.year
    try:
        local_date = date(year, month, int(match.group("day")))
        if match.group("year") is None and local_date < current.date():
            local_date = date(year + 1, month, int(match.group("day")))
    except ValueError:
        return None
    return TemporalCandidate(
        original_phrase=match.group(0).strip(),
        normalized_value=datetime.combine(
            local_date,
            time(23, 59, 59),
            tzinfo=ZoneInfo(context.timezone),
        ),
        status=TemporalStatus.RESOLVED,
        explanation=None,
        time_was_explicit=False,
    )


def remove_temporal_ambiguities(values: list[str]) -> list[str]:
    return [
        value
        for value in values
        if not any(marker in value.casefold() for marker in TEMPORAL_AMBIGUITY_MARKERS)
    ]


def apply_exact_temporal_answer(
    current: DraftAnalysisResult,
    answer: str,
    question: str,
) -> DraftParseResult | None:
    question_key = question.casefold()
    if "напомин" not in question_key and "срок" not in question_key:
        return None
    candidate = parse_exact_local_date(answer, current.context)
    if candidate is None:
        return None
    parsed = analysis_to_parse_result(current)
    items = list(parsed.draft_items)
    for index, item in enumerate(items):
        if item.reminder_candidate is None and item.due_date_candidate is None:
            continue
        items[index] = DraftItem.model_validate(
            {
                **item.model_dump(),
                "due_date_candidate": candidate,
                "reminder_candidate": None,
                "ambiguities": remove_temporal_ambiguities(item.ambiguities),
            }
        )
        return DraftParseResult.model_validate(
            {
                **parsed.model_dump(),
                "draft_items": items,
                "ambiguities": remove_temporal_ambiguities(parsed.ambiguities),
            }
        )
    return None


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
        context: DraftInputContext | None = None,
    ) -> DraftAnalysisResult:
        normalized = user_text.strip()
        if not normalized:
            raise AIInvalidResponseError("note text must not be empty")

        parse_context = context or self._build_context(source)
        return await self._parse_with_prompt(
            user_text=normalized,
            system_prompt=build_system_prompt(parse_context),
            context=parse_context,
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

        exact_temporal = apply_exact_temporal_answer(current, normalized, question)
        if exact_temporal is not None:
            return build_analysis_result(
                exact_temporal,
                context=current.context,
                high_threshold=self._high_confidence_threshold,
                clarification_threshold=self._clarification_confidence_threshold,
            )

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

    async def parse_text(
        self,
        user_text: str,
    ) -> DraftAnalysisResult | ManagementIntent | SearchIntent:
        normalized = user_text.strip()
        if not normalized:
            raise AIInvalidResponseError("note text must not be empty")
        context = self._build_context(DraftSource.TEXT)
        if not isinstance(self._provider, TextRoutingProvider):
            raise AIInvalidResponseError("AI provider does not support text routing")
        try:
            async with asyncio.timeout(self._timeout_seconds):
                parsed = await self._provider.parse_text(
                    system_prompt=build_text_routing_prompt(context),
                    user_text=normalized,
                )
        except TimeoutError as error:
            raise AITimeoutError("AI text routing timed out") from error
        if not isinstance(parsed, TelegramTextParseResult):
            raise AIInvalidResponseError("AI provider returned invalid text routing")
        if parsed.mode == "management":
            if parsed.management is None:
                raise AIInvalidResponseError("management payload is missing")
            return parsed.management
        if parsed.mode == "search":
            if parsed.search is None:
                raise AIInvalidResponseError("search payload is missing")
            return parsed.search
        if parsed.draft is None:
            raise AIInvalidResponseError("draft payload is missing")
        return build_analysis_result(
            parsed.draft,
            context=context,
            high_threshold=self._high_confidence_threshold,
            clarification_threshold=self._clarification_confidence_threshold,
        )

    def _build_context(self, source: DraftSource) -> DraftInputContext:
        return DraftInputContext(
            current_datetime=self._clock(self._timezone),
            timezone=self._timezone.key,
            active_workspace=self._active_workspace,
            channel="telegram",
            source=source,
        )

    def build_meeting_context(
        self,
        *,
        source: DraftSource,
        timezone: ZoneInfo,
        current_datetime: datetime,
        meeting: MeetingDraftContext,
    ) -> DraftInputContext:
        return DraftInputContext(
            current_datetime=current_datetime.astimezone(timezone),
            timezone=timezone.key,
            active_workspace=self._active_workspace,
            channel="telegram",
            source=source,
            meeting=meeting,
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
