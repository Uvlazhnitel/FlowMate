from datetime import UTC, datetime

from flowmate.ai.analysis import build_analysis_result
from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftInputContext,
    DraftItem,
    DraftItemType,
    DraftParseResult,
    DraftSource,
    TemporalCandidate,
    TemporalStatus,
)


def make_temporal_candidate(**overrides: object) -> TemporalCandidate:
    values: dict[str, object] = {
        "original_phrase": "tomorrow at 09:00",
        "normalized_value": datetime(2026, 7, 21, 9, tzinfo=UTC),
        "status": TemporalStatus.RESOLVED,
        "explanation": None,
        "time_was_explicit": True,
    }
    values.update(overrides)
    return TemporalCandidate.model_validate(values)


def make_draft_item(**overrides: object) -> DraftItem:
    values: dict[str, object] = {
        "type": DraftItemType.TASK,
        "title": "Prepare report",
        "description": None,
        "person_candidates": [],
        "topic_candidates": [],
        "due_date_candidate": None,
        "reminder_candidate": None,
        "notes": [],
        "missing_fields": [],
        "ambiguities": [],
        "dependencies": [],
        "confidence": 0.9,
    }
    values.update(overrides)
    return DraftItem.model_validate(values)


def make_parse_result(
    items: list[DraftItem] | None = None,
    **overrides: object,
) -> DraftParseResult:
    values: dict[str, object] = {
        "overall_intent": DraftItemType.TASK,
        "draft_items": items or [make_draft_item()],
        "ambiguities": [],
        "confidence": 0.9,
    }
    values.update(overrides)
    return DraftParseResult.model_validate(values)


def make_context(**overrides: object) -> DraftInputContext:
    values: dict[str, object] = {
        "current_datetime": datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
        "timezone": "UTC",
        "active_workspace": "personal",
        "channel": "telegram",
        "source": DraftSource.TEXT,
    }
    values.update(overrides)
    return DraftInputContext.model_validate(values)


def make_analysis_result(
    result: DraftParseResult | None = None,
    *,
    context: DraftInputContext | None = None,
    high_threshold: float = 0.8,
    clarification_threshold: float = 0.5,
) -> DraftAnalysisResult:
    return build_analysis_result(
        result or make_parse_result(),
        context=context or make_context(),
        high_threshold=high_threshold,
        clarification_threshold=clarification_threshold,
    )
