from datetime import datetime, time
from zoneinfo import ZoneInfo

from flowmate.ai.schemas import (
    DependencyCandidate,
    DraftAnalysisResult,
    DraftInputContext,
    DraftItem,
    DraftItemAssessment,
    DraftItemType,
    DraftParseResult,
    DraftReadiness,
    TemporalCandidate,
    TemporalStatus,
)


def normalize_text(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def temporal_key(candidate: TemporalCandidate | None) -> tuple[object, ...] | None:
    if candidate is None:
        return None
    normalized = (
        candidate.normalized_value.isoformat()
        if candidate.normalized_value is not None
        else None
    )
    unresolved_phrase = (
        normalize_text(candidate.original_phrase) if normalized is None else None
    )
    return candidate.status, normalized, unresolved_phrase


def item_key(item: DraftItem) -> tuple[object, ...]:
    return (
        item.type,
        normalize_text(item.title),
        normalize_text(item.description),
        temporal_key(item.due_date_candidate),
        temporal_key(item.reminder_candidate),
    )


def unique_texts(*groups: list[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            key = normalize_text(value)
            if key not in seen:
                seen.add(key)
                values.append(value)
    return values


def normalize_due_date(item: DraftItem, timezone: ZoneInfo) -> DraftItem:
    candidate = item.due_date_candidate
    if (
        candidate is None
        or candidate.status is not TemporalStatus.RESOLVED
        or candidate.time_was_explicit
        or candidate.normalized_value is None
    ):
        return item

    local_date = candidate.normalized_value.astimezone(timezone).date()
    end_of_day = datetime.combine(local_date, time(23, 59, 59), tzinfo=timezone)
    normalized_candidate = TemporalCandidate.model_validate(
        {
            **candidate.model_dump(),
            "normalized_value": end_of_day,
        }
    )
    return DraftItem.model_validate(
        {
            **item.model_dump(),
            "due_date_candidate": normalized_candidate,
        }
    )


def merge_duplicate_item(existing: DraftItem, duplicate: DraftItem) -> DraftItem:
    return DraftItem.model_validate(
        {
            **existing.model_dump(),
            "person_candidates": unique_texts(
                existing.person_candidates,
                duplicate.person_candidates,
            ),
            "topic_candidates": unique_texts(
                existing.topic_candidates,
                duplicate.topic_candidates,
            ),
            "notes": unique_texts(existing.notes, duplicate.notes),
            "missing_fields": unique_texts(
                existing.missing_fields,
                duplicate.missing_fields,
            ),
            "ambiguities": unique_texts(
                existing.ambiguities,
                duplicate.ambiguities,
            ),
            "dependencies": [
                *existing.dependencies,
                *duplicate.dependencies,
            ],
            "confidence": min(existing.confidence, duplicate.confidence),
        }
    )


def remap_dependencies(
    items: list[DraftItem],
    old_to_new: dict[int, int],
) -> list[DraftItem]:
    remapped_items: list[DraftItem] = []
    for item_number, item in enumerate(items, start=1):
        dependencies: list[DependencyCandidate] = []
        seen: set[tuple[object, ...]] = set()
        for dependency in item.dependencies:
            target = dependency.target_item_number
            remapped_target = old_to_new[target] if target is not None else None
            if remapped_target == item_number:
                continue
            remapped = DependencyCandidate.model_validate(
                {
                    **dependency.model_dump(),
                    "target_item_number": remapped_target,
                }
            )
            key = (
                remapped.relation,
                remapped.target_item_number,
                normalize_text(remapped.original_phrase),
                normalize_text(remapped.condition),
            )
            if key not in seen:
                seen.add(key)
                dependencies.append(remapped)
        remapped_items.append(
            DraftItem.model_validate(
                {**item.model_dump(), "dependencies": dependencies}
            )
        )
    return remapped_items


def deduplicate_items(
    items: list[DraftItem],
    *,
    timezone: ZoneInfo,
) -> list[DraftItem]:
    merged: list[DraftItem] = []
    key_to_new_number: dict[tuple[object, ...], int] = {}
    old_to_new: dict[int, int] = {}

    for old_number, raw_item in enumerate(items, start=1):
        item = normalize_due_date(raw_item, timezone)
        key = item_key(item)
        new_number = key_to_new_number.get(key)
        if new_number is None:
            merged.append(item)
            new_number = len(merged)
            key_to_new_number[key] = new_number
        else:
            merged[new_number - 1] = merge_duplicate_item(
                merged[new_number - 1],
                item,
            )
        old_to_new[old_number] = new_number

    return remap_dependencies(merged, old_to_new)


def classify_readiness(
    item: DraftItem,
    *,
    result_ambiguities: list[str],
    high_threshold: float,
    clarification_threshold: float,
) -> DraftReadiness:
    temporal_candidates = (
        item.due_date_candidate,
        item.reminder_candidate,
    )
    if any(
        candidate is not None and candidate.status is TemporalStatus.INVALID
        for candidate in temporal_candidates
    ):
        return DraftReadiness.UNRESOLVED
    if item.confidence < clarification_threshold:
        return DraftReadiness.UNRESOLVED
    if item.type is DraftItemType.UNKNOWN:
        return DraftReadiness.CLARIFICATION_REQUIRED
    # Provider-reported missing details remain useful Inbox metadata, but they
    # must not turn optional fields such as amount, topic, or date into blockers.
    identity_is_ambiguous = bool(item.ambiguities) and (
        len(item.person_candidates) > 1 or len(item.topic_candidates) > 1
    )
    needs_clarification = (
        item.confidence < high_threshold
        or identity_is_ambiguous
        or any(
            candidate is not None and candidate.status is TemporalStatus.AMBIGUOUS
            for candidate in temporal_candidates
        )
    )
    if needs_clarification:
        return DraftReadiness.CLARIFICATION_REQUIRED
    return DraftReadiness.READY


def build_analysis_result(
    result: DraftParseResult,
    *,
    context: DraftInputContext,
    high_threshold: float,
    clarification_threshold: float,
) -> DraftAnalysisResult:
    timezone = ZoneInfo(context.timezone)
    items = deduplicate_items(result.draft_items, timezone=timezone)
    assessments = [
        DraftItemAssessment(
            item=item,
            readiness=classify_readiness(
                item,
                result_ambiguities=result.ambiguities,
                high_threshold=high_threshold,
                clarification_threshold=clarification_threshold,
            ),
        )
        for item in items
    ]
    return DraftAnalysisResult(
        context=context,
        overall_intent=result.overall_intent,
        items=assessments,
        ambiguities=result.ambiguities,
        confidence=result.confidence,
    )


def analysis_to_parse_result(analysis: DraftAnalysisResult) -> DraftParseResult:
    return DraftParseResult(
        overall_intent=analysis.overall_intent,
        draft_items=[assessment.item for assessment in analysis.items],
        ambiguities=analysis.ambiguities,
        confidence=analysis.confidence,
    )
