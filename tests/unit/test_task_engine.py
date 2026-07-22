from datetime import UTC, datetime

import pytest

from flowmate.task_engine.enums import (
    NoteTargetType,
    WorkItemEventType,
    WorkItemPriority,
    WorkItemRelationType,
    WorkItemStatus,
    WorkItemType,
)
from flowmate.task_engine.service import (
    normalize_aliases,
    normalize_required_text,
    parse_event_type,
    parse_note_target_type,
    parse_relation_type,
    parse_work_item_priority,
    parse_work_item_status,
    parse_work_item_type,
    validate_aware_datetime,
)


def test_task_engine_enum_contracts() -> None:
    assert {value.value for value in WorkItemType} == {
        "task",
        "follow_up",
        "waiting",
        "question",
        "decision",
        "agenda_item",
    }
    assert {value.value for value in WorkItemStatus} == {
        "inbox",
        "planned",
        "active",
        "waiting",
        "snoozed",
        "done",
        "cancelled",
        "archived",
    }
    assert {value.value for value in WorkItemPriority} == {
        "low",
        "normal",
        "high",
        "urgent",
    }
    assert {value.value for value in WorkItemRelationType} == {
        "related_to",
        "blocked_by",
        "after_completion",
        "created_from",
        "waiting_for",
    }
    assert {value.value for value in WorkItemEventType} == {
        "created",
        "updated",
        "status_changed",
        "linked",
        "completed",
        "reopened",
        "cancelled",
        "rescheduled",
        "note_added",
        "topic_changed",
        "person_changed",
        "waiting_received",
        "person_replied",
        "reminder_snoozed",
        "planner_status_changed",
        "archived",
    }
    assert {value.value for value in NoteTargetType} == {
        "work_item",
        "person",
        "topic",
    }


@pytest.mark.parametrize(
    "parser",
    [
        parse_work_item_type,
        parse_work_item_status,
        parse_work_item_priority,
        parse_relation_type,
        parse_event_type,
        parse_note_target_type,
    ],
)
def test_task_engine_rejects_invalid_enum_values(parser: object) -> None:
    with pytest.raises(ValueError):
        parser("invalid")  # type: ignore[operator]


def test_text_and_alias_normalization() -> None:
    assert normalize_required_text("  Client   Alpha  ", "name") == "Client Alpha"
    assert normalize_aliases(
        [" Alpha ", "ALPHA", " Project X ", "", "Client Alpha"],
        "Client Alpha",
    ) == ["alpha", "project x"]
    with pytest.raises(ValueError, match="name must not be blank"):
        normalize_required_text("  ", "name")


def test_task_engine_requires_timezone_aware_datetimes() -> None:
    validate_aware_datetime(datetime(2026, 7, 21, tzinfo=UTC), "due_at")
    validate_aware_datetime(None, "due_at")

    with pytest.raises(ValueError, match="due_at must be timezone-aware"):
        validate_aware_datetime(datetime(2026, 7, 21), "due_at")
