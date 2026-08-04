import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

from flowmate.ai.schemas import (
    DependencyCandidate,
    DependencyRelation,
    DraftAnalysisResult,
    DraftInputContext,
    DraftItem,
    DraftItemAssessment,
    DraftItemType,
    DraftParseResult,
    DraftReadiness,
    ItemizationBasis,
    ItemizationDecision,
    TemporalCandidate,
    TemporalStatus,
)

SINGLE_ITEM_DIRECTIVE = re.compile(
    r"\b(?:одна\s+задача|одним\s+пунктом|не\s+разделяй)\b",
    re.IGNORECASE,
)
MULTIPLE_ITEMS_DIRECTIVE = re.compile(
    r"\b(?:две\s+задачи|несколько\s+задач)\b",
    re.IGNORECASE,
)
MULTIPLE_ITEM_BASES = {
    ItemizationBasis.EXPLICIT_LIST,
    ItemizationBasis.SEPARATE_SENTENCES,
    ItemizationBasis.INDEPENDENT_OUTCOMES,
}
EXPLICIT_FOLLOW_UP = re.compile(
    r"(?:"
    r"\bfollow[\s-]?up\b|"
    r"\bфол+оу[\s-]?ап\w*\b|"  # noqa: RUF001
    r"\b(?:проверить|уточнить)\s+статус\b|"
    r"\bпроверить[,\s]+(?:ответил|ответила|ответили)\b|"  # noqa: RUF001
    r"\bнапомнить\s+о\s+себе\b|"  # noqa: RUF001
    r"\bсвязаться\s+(?:снова|повторно)\b|"  # noqa: RUF001
    r"\b(?:снова|повторно)\s+(?:связаться|написать|позвонить)\b|"
    r"\b(?:пингануть|дожать)\b|"
    r"\bcheck[\s-]?back\b"
    r")",
    re.IGNORECASE,
)
DIRECT_CONTACT_FOLLOW_UP = re.compile(
    r"^\s*(?:по|пере)?звонить\b|^\s*связаться\b",
    re.IGNORECASE,
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


def explicit_itemization_decision(value: str) -> ItemizationDecision | None:
    if SINGLE_ITEM_DIRECTIVE.search(value):
        return ItemizationDecision.SINGLE
    if MULTIPLE_ITEMS_DIRECTIVE.search(value):
        return ItemizationDecision.MULTIPLE
    return None


def apply_itemization_policy(
    result: DraftParseResult,
    *,
    source_text: str,
    split_threshold: float,
) -> DraftParseResult:
    if result.itemization_decision is ItemizationDecision.SINGLE:
        return result

    explicit = explicit_itemization_decision(source_text)
    keep_multiple = explicit is ItemizationDecision.MULTIPLE or (
        explicit is not ItemizationDecision.SINGLE
        and result.itemization_basis in MULTIPLE_ITEM_BASES
        and result.itemization_confidence >= split_threshold
    )
    if keep_multiple:
        return result

    fallback = result.consolidated_item
    if fallback is None:  # Protected by DraftParseResult validation.
        raise ValueError("multiple itemization has no consolidated fallback")
    return result.model_copy(
        update={
            "overall_intent": fallback.type,
            "draft_items": [fallback],
            "itemization_decision": ItemizationDecision.SINGLE,
            "itemization_basis": (
                ItemizationBasis.SINGLE_GOAL
                if explicit is ItemizationDecision.SINGLE
                else ItemizationBasis.UNCERTAIN
            ),
            "consolidated_item": None,
        }
    )


def apply_item_type_policy(
    result: DraftParseResult,
    *,
    source_text: str,
) -> DraftParseResult:
    """Correct only explicit, low-risk task/follow-up classification mistakes."""
    single_item = len(result.draft_items) == 1
    items: list[DraftItem] = []
    for item in result.draft_items:
        item_text = " ".join(
            value for value in (item.title, item.description) if value is not None
        )
        explicit_follow_up = bool(EXPLICIT_FOLLOW_UP.search(item_text))
        if single_item:
            explicit_follow_up = explicit_follow_up or bool(
                EXPLICIT_FOLLOW_UP.search(source_text)
            )
        direct_contact = bool(
            item.person_candidates and DIRECT_CONTACT_FOLLOW_UP.search(item.title)
        )
        if item.type is DraftItemType.TASK and (explicit_follow_up or direct_contact):
            item = item.model_copy(update={"type": DraftItemType.FOLLOW_UP})
        items.append(item)

    overall_intent = result.overall_intent
    if len(items) == 1:
        overall_intent = items[0].type
    return result.model_copy(
        update={
            "overall_intent": overall_intent,
            "draft_items": items,
        }
    )


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


def materialize_external_conditions(item: DraftItem) -> DraftItem:
    conditions = unique_texts(
        [
            dependency.condition
            for dependency in item.dependencies
            if dependency.relation is DependencyRelation.CONDITIONAL
            and dependency.target_item_number is None
            and dependency.condition is not None
        ]
    )
    if not conditions:
        return item
    current_description = normalize_text(item.description)
    additions = [
        f"Условие: {condition}"
        for condition in conditions
        if normalize_text(f"Условие: {condition}") not in current_description
    ]
    if not additions:
        return item
    description = "\n\n".join(
        value
        for value in (item.description, *additions)
        if value is not None and value.strip()
    )
    return item.model_copy(update={"description": description})


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
        item = materialize_external_conditions(normalize_due_date(raw_item, timezone))
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
    needs_clarification = item.confidence < high_threshold or any(
        candidate is not None and candidate.status is TemporalStatus.AMBIGUOUS
        for candidate in temporal_candidates
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
        itemization_decision=(
            result.itemization_decision
            if len(items) > 1
            else ItemizationDecision.SINGLE
        ),
        itemization_basis=(
            result.itemization_basis if len(items) > 1 else ItemizationBasis.SINGLE_GOAL
        ),
        itemization_confidence=result.itemization_confidence,
        consolidated_item=result.consolidated_item if len(items) > 1 else None,
        ambiguities=result.ambiguities,
        confidence=result.confidence,
        workspace_candidate=result.workspace_candidate,
        workspace_confidence=result.workspace_confidence,
    )


def analysis_to_parse_result(analysis: DraftAnalysisResult) -> DraftParseResult:
    items = [assessment.item for assessment in analysis.items]
    is_multiple = len(items) > 1
    return DraftParseResult(
        overall_intent=analysis.overall_intent,
        draft_items=items,
        itemization_decision=(
            ItemizationDecision.MULTIPLE if is_multiple else ItemizationDecision.SINGLE
        ),
        itemization_basis=(
            analysis.itemization_basis if is_multiple else ItemizationBasis.SINGLE_GOAL
        ),
        itemization_confidence=analysis.itemization_confidence,
        consolidated_item=(
            analysis.consolidated_item or items[0] if is_multiple else None
        ),
        ambiguities=analysis.ambiguities,
        confidence=analysis.confidence,
        workspace_candidate=analysis.workspace_candidate,
        workspace_confidence=analysis.workspace_confidence,
    )
