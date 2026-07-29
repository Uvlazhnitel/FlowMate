from datetime import UTC, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from flowmate.db.models import WorkItem
from flowmate.reminders.parsing import SnoozeParsingService
from flowmate.reminders.preferences import EffectiveNotificationPreferences
from flowmate.task_engine.enums import WorkItemStatus, WorkItemType
from flowmate.task_engine.rescheduling import (
    LATER_TODAY_UNAVAILABLE_MESSAGE,
    LaterTodayUnavailableError,
    ReschedulePreset,
    ReschedulingService,
)


def preferences(
    timezone: str = "UTC",
    *,
    default_reminder_time: time = time(8, 30),
) -> EffectiveNotificationPreferences:
    return EffectiveNotificationPreferences(
        timezone=timezone,
        morning_digest_enabled=False,
        morning_digest_time=time(9),
        evening_digest_enabled=False,
        evening_digest_time=time(18),
        quiet_hours_enabled=False,
        quiet_hours_start=time(22),
        quiet_hours_end=time(8),
        default_reminder_time=default_reminder_time,
        default_snooze_minutes=30,
        send_empty_digests=False,
        date_display_format="day_month_year",
        time_display_format="24h",
    )


def work_item(
    *,
    item_type: WorkItemType = WorkItemType.TASK,
    due_at: datetime | None = None,
    next_follow_up_at: datetime | None = None,
) -> WorkItem:
    return WorkItem(
        id=uuid4(),
        user_id=uuid4(),
        type=item_type.value,
        title="Test",
        status=WorkItemStatus.ACTIVE.value,
        due_at=due_at,
        next_follow_up_at=next_follow_up_at,
    )


@pytest.mark.parametrize(
    ("preset", "expected"),
    [
        (
            ReschedulePreset.LATER_TODAY,
            datetime(2026, 7, 30, 13, 15, tzinfo=UTC),
        ),
        (
            ReschedulePreset.TOMORROW_MORNING,
            datetime(2026, 7, 31, 8, 30, tzinfo=UTC),
        ),
        (
            ReschedulePreset.NEXT_WORKING_DAY,
            datetime(2026, 7, 31, 16, 45, tzinfo=UTC),
        ),
        (
            ReschedulePreset.NEXT_WEEK,
            datetime(2026, 8, 6, 16, 45, tzinfo=UTC),
        ),
    ],
)
def test_reschedule_presets(
    preset: ReschedulePreset,
    expected: datetime,
) -> None:
    service = ReschedulingService(SnoozeParsingService(None, timeout_seconds=5))
    item = work_item(due_at=datetime(2026, 7, 30, 16, 45, tzinfo=UTC))

    result = service.resolve_preset(
        item,
        preset,
        preferences=preferences(),
        now=datetime(2026, 7, 30, 10, 7, tzinfo=UTC),
    )

    assert result == expected


def test_next_working_day_from_friday_is_monday() -> None:
    service = ReschedulingService(SnoozeParsingService(None, timeout_seconds=5))
    result = service.resolve_preset(
        work_item(due_at=datetime(2026, 7, 31, 14, tzinfo=UTC)),
        ReschedulePreset.NEXT_WORKING_DAY,
        preferences=preferences(),
        now=datetime(2026, 7, 31, 10, tzinfo=UTC),
    )

    assert result == datetime(2026, 8, 3, 14, tzinfo=UTC)


def test_presets_without_current_deadline_use_default_reminder_time() -> None:
    service = ReschedulingService(SnoozeParsingService(None, timeout_seconds=5))
    result = service.resolve_preset(
        work_item(),
        ReschedulePreset.NEXT_WEEK,
        preferences=preferences(default_reminder_time=time(7, 45)),
        now=datetime(2026, 7, 30, 10, tzinfo=UTC),
    )

    assert result == datetime(2026, 8, 6, 7, 45, tzinfo=UTC)


def test_follow_up_uses_next_follow_up_time() -> None:
    service = ReschedulingService(SnoozeParsingService(None, timeout_seconds=5))
    result = service.resolve_preset(
        work_item(
            item_type=WorkItemType.FOLLOW_UP,
            due_at=datetime(2026, 7, 30, 8, tzinfo=UTC),
            next_follow_up_at=datetime(2026, 7, 30, 17, 30, tzinfo=UTC),
        ),
        ReschedulePreset.NEXT_WEEK,
        preferences=preferences(),
        now=datetime(2026, 7, 30, 10, tzinfo=UTC),
    )

    assert result == datetime(2026, 8, 6, 17, 30, tzinfo=UTC)


def test_next_week_preserves_local_time_across_dst() -> None:
    service = ReschedulingService(SnoozeParsingService(None, timeout_seconds=5))
    timezone = ZoneInfo("Europe/Riga")
    result = service.resolve_preset(
        work_item(due_at=datetime(2026, 3, 24, 13, tzinfo=UTC)),
        ReschedulePreset.NEXT_WEEK,
        preferences=preferences("Europe/Riga"),
        now=datetime(2026, 3, 24, 10, tzinfo=UTC),
    )

    assert result.astimezone(timezone).date().isoformat() == "2026-03-31"
    assert result.astimezone(timezone).time() == time(15)
    assert result == datetime(2026, 3, 31, 12, tzinfo=UTC)


def test_later_today_rejects_next_day() -> None:
    service = ReschedulingService(SnoozeParsingService(None, timeout_seconds=5))

    with pytest.raises(
        LaterTodayUnavailableError,
        match=LATER_TODAY_UNAVAILABLE_MESSAGE,
    ):
        service.resolve_preset(
            work_item(),
            ReschedulePreset.LATER_TODAY,
            preferences=preferences(),
            now=datetime(2026, 7, 30, 21, 1, tzinfo=UTC),
        )


def test_later_today_rounds_up_when_candidate_has_seconds() -> None:
    service = ReschedulingService(SnoozeParsingService(None, timeout_seconds=5))

    result = service.resolve_preset(
        work_item(),
        ReschedulePreset.LATER_TODAY,
        preferences=preferences(),
        now=datetime(2026, 7, 30, 10, 0, 1, tzinfo=UTC),
    )

    assert result == datetime(2026, 7, 30, 13, 15, tzinfo=UTC)
