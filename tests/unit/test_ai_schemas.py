import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from flowmate.ai.schemas import (
    DependencyCandidate,
    DependencyRelation,
    DraftItem,
    DraftItemType,
    DraftParseResult,
    ManagementAction,
    ManagementIntent,
    SearchIntent,
    SearchWorkItemStatus,
    SearchWorkItemType,
    TelegramTextParseResult,
    TemporalCandidate,
    TemporalStatus,
)
from tests.ai_factories import make_draft_item, make_parse_result


def temporal_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "original_phrase": "tomorrow at 09:00",
        "normalized_value": datetime(2026, 7, 21, 9, tzinfo=UTC),
        "status": TemporalStatus.RESOLVED,
        "explanation": None,
        "time_was_explicit": True,
    }
    values.update(overrides)
    return values


def item_payload(**overrides: object) -> dict[str, object]:
    values = make_draft_item().model_dump()
    values.update(overrides)
    return values


def result_payload(**overrides: object) -> dict[str, object]:
    values = make_parse_result().model_dump()
    values.update(overrides)
    return values


def test_supports_one_item_and_unknown_intent() -> None:
    result = make_parse_result(
        [make_draft_item(type=DraftItemType.UNKNOWN)],
        overall_intent=DraftItemType.UNKNOWN,
    )

    assert result.overall_intent is DraftItemType.UNKNOWN
    assert result.draft_items[0].type is DraftItemType.UNKNOWN


def test_supports_multiple_mixed_language_items_and_candidates() -> None:
    result = make_parse_result(
        [
            make_draft_item(
                type=DraftItemType.QUESTION,
                title="Спросить lead про escalation",
                person_candidates=["lead"],
                topic_candidates=["эскалация", "escalation"],
            ),
            make_draft_item(
                type=DraftItemType.FOLLOW_UP,
                title="Write Антону по срокам",
                person_candidates=["Антон"],
                topic_candidates=["сроки"],
            ),
            make_draft_item(
                type=DraftItemType.NOTE,
                title="Клиент ждёт ответ",
            ),
        ]
    )

    assert [item.type for item in result.draft_items] == [
        DraftItemType.QUESTION,
        DraftItemType.FOLLOW_UP,
        DraftItemType.NOTE,
    ]
    assert result.draft_items[1].person_candidates == ["Антон"]


@pytest.mark.parametrize(
    "payload",
    [
        result_payload(extra_field="forbidden"),
        result_payload(draft_items=[]),
        result_payload(draft_items=[item_payload(title="  ")]),
        result_payload(confidence=-0.01),
        result_payload(confidence=1.01),
        result_payload(draft_items=[item_payload(confidence=-0.01)]),
        result_payload(draft_items=[item_payload(confidence=1.01)]),
    ],
)
def test_rejects_invalid_structured_drafts(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DraftParseResult.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        temporal_payload(normalized_value=None),
        temporal_payload(
            normalized_value=datetime(2026, 7, 21, 9),
        ),
        temporal_payload(
            status=TemporalStatus.AMBIGUOUS,
            explanation="Could mean two dates",
        ),
        temporal_payload(
            status=TemporalStatus.AMBIGUOUS,
            normalized_value=None,
            explanation=None,
        ),
        temporal_payload(
            status=TemporalStatus.INVALID,
            normalized_value=None,
            explanation=None,
        ),
    ],
)
def test_rejects_inconsistent_or_naive_temporal_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TemporalCandidate.model_validate(payload)


def test_rejects_resolved_reminder_without_explicit_time() -> None:
    reminder = temporal_payload(time_was_explicit=False)

    with pytest.raises(ValidationError, match="reminder"):
        DraftItem.model_validate(item_payload(reminder_candidate=reminder))


@pytest.mark.parametrize(
    "dependency",
    [
        {
            "relation": DependencyRelation.AFTER,
            "original_phrase": "после этого",
            "target_item_number": None,
            "condition": None,
        },
        {
            "relation": DependencyRelation.BLOCKED_BY,
            "original_phrase": "заблокировано до ответа",
            "target_item_number": None,
            "condition": None,
        },
        {
            "relation": DependencyRelation.WAITING_FOR,
            "original_phrase": "ждёт результата",
            "target_item_number": None,
            "condition": None,
        },
        {
            "relation": DependencyRelation.CONDITIONAL,
            "original_phrase": "если согласуют",
            "target_item_number": None,
            "condition": None,
        },
    ],
)
def test_rejects_incomplete_dependencies(dependency: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DependencyCandidate.model_validate(dependency)


@pytest.mark.parametrize("target", [1, 3])
def test_rejects_self_or_out_of_range_dependency(target: int) -> None:
    dependency = DependencyCandidate(
        relation=DependencyRelation.AFTER,
        original_phrase="после этого",
        target_item_number=target,
        condition=None,
    )
    items = [
        make_draft_item(dependencies=[dependency]),
        make_draft_item(title="Second item"),
    ]

    with pytest.raises(ValidationError):
        DraftParseResult(
            overall_intent=DraftItemType.TASK,
            draft_items=items,
            ambiguities=[],
            confidence=0.9,
        )


def test_json_validation_accepts_timezone_aware_iso_datetime() -> None:
    payload = result_payload(
        draft_items=[
            item_payload(
                due_date_candidate={
                    **temporal_payload(),
                    "normalized_value": "2026-07-21T09:00:00+03:00",
                    "status": "resolved",
                }
            )
        ],
        overall_intent="task",
    )

    result = DraftParseResult.model_validate_json(json.dumps(payload, default=str))

    due = result.draft_items[0].due_date_candidate
    assert due is not None
    assert due.normalized_value is not None
    assert due.normalized_value.utcoffset() is not None


def test_json_validation_rejects_impossible_normalized_date() -> None:
    payload = result_payload(
        draft_items=[
            item_payload(
                due_date_candidate={
                    **temporal_payload(),
                    "normalized_value": "2026-02-30T09:00:00+00:00",
                    "status": "resolved",
                }
            )
        ],
        overall_intent="task",
    )

    with pytest.raises(ValidationError):
        DraftParseResult.model_validate_json(json.dumps(payload, default=str))


def test_draft_item_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DraftItem.model_validate({**item_payload(), "unexpected": "value"})


def management_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "action": ManagementAction.RESCHEDULE,
        "target_type": DraftItemType.TASK,
        "record_query": "подготовить отчёт",
        "contextual_reference": False,
        "person_candidate": None,
        "topic_candidate": None,
        "note_text": None,
        "temporal_candidate": temporal_payload(),
        "missing_fields": [],
        "ambiguities": [],
        "confidence": 0.95,
    }
    values.update(overrides)
    return values


def test_text_envelope_accepts_strict_management_intent() -> None:
    result = TelegramTextParseResult.model_validate(
        {"mode": "management", "draft": None, "management": management_payload()}
    )

    assert result.management is not None
    assert result.management.action is ManagementAction.RESCHEDULE
    assert result.management.temporal_candidate is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "management", "draft": None, "management": None},
        {
            "mode": "management",
            "draft": result_payload(),
            "management": management_payload(),
        },
        {"mode": "new_draft", "draft": None, "management": management_payload()},
        {
            "mode": "management",
            "draft": None,
            "management": management_payload(confidence=1.1),
        },
        {
            "mode": "management",
            "draft": None,
            "management": management_payload(extra="forbidden"),
        },
    ],
)
def test_text_envelope_rejects_invalid_payloads(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TelegramTextParseResult.model_validate(payload)


def test_management_intent_rejects_naive_date() -> None:
    with pytest.raises(ValidationError):
        ManagementIntent.model_validate(
            management_payload(
                temporal_candidate=temporal_payload(
                    normalized_value=datetime(2026, 7, 21, 9)
                )
            )
        )


def search_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "text_query": None,
        "person_query": "Антон",
        "topic_query": None,
        "item_types": [SearchWorkItemType.FOLLOW_UP],
        "statuses": [SearchWorkItemStatus.ACTIVE],
        "include_all_statuses": False,
        "due_from": None,
        "due_to": None,
        "overdue": False,
        "stale_contacts": False,
        "ambiguities": [],
        "confidence": 0.93,
    }
    values.update(overrides)
    return values


def test_text_envelope_accepts_strict_search_intent() -> None:
    result = TelegramTextParseResult.model_validate(
        {"mode": "search", "search": search_payload()}
    )

    assert isinstance(result.search, SearchIntent)
    assert result.search.person_query == "Антон"


@pytest.mark.parametrize(
    "payload",
    [
        search_payload(statuses=["done"], include_all_statuses=True),
        search_payload(
            due_from="2026-07-22T12:00:00+00:00",
            due_to="2026-07-22T10:00:00+00:00",
        ),
        search_payload(
            due_from="2026-07-22T00:00:00+00:00",
            overdue=True,
        ),
        search_payload(confidence=-0.1),
        {**search_payload(), "extra": "forbidden"},
    ],
)
def test_search_intent_rejects_invalid_filters(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SearchIntent.model_validate(payload)
