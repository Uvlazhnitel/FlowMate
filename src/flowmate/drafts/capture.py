from datetime import time
from zoneinfo import ZoneInfo

from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftItemAssessment,
    DraftItemType,
    DraftReadiness,
    TemporalStatus,
)
from flowmate.reminders.timezone import resolve_local_datetime


def apply_default_reminder_time(
    analysis: DraftAnalysisResult,
    *,
    default_time: time,
) -> DraftAnalysisResult:
    timezone = ZoneInfo(analysis.context.timezone)
    changed = False
    assessments: list[DraftItemAssessment] = []
    for assessment in analysis.items:
        item = assessment.item
        candidate = item.reminder_candidate
        if (
            candidate is None
            or candidate.status is not TemporalStatus.RESOLVED
            or candidate.normalized_value is None
            or candidate.time_was_explicit
        ):
            assessments.append(assessment)
            continue
        local_date = candidate.normalized_value.astimezone(timezone).date()
        resolved = resolve_local_datetime(local_date, default_time, timezone)
        updated_candidate = candidate.model_copy(update={"normalized_value": resolved})
        updated_item = item.model_copy(update={"reminder_candidate": updated_candidate})
        assessments.append(assessment.model_copy(update={"item": updated_item}))
        changed = True
    return analysis.model_copy(update={"items": assessments}) if changed else analysis


def fast_capture_is_ready(
    analysis: DraftAnalysisResult,
    *,
    high_confidence_threshold: float,
) -> bool:
    if analysis.confidence < high_confidence_threshold:
        return False
    for assessment in analysis.items:
        item = assessment.item
        if (
            assessment.readiness is not DraftReadiness.READY
            or item.type is DraftItemType.UNKNOWN
            or item.confidence < high_confidence_threshold
        ):
            return False
        if any(
            candidate is not None and candidate.status is not TemporalStatus.RESOLVED
            for candidate in (item.due_date_candidate, item.reminder_candidate)
        ):
            return False
    return True
