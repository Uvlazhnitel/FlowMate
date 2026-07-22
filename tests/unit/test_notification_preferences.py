from datetime import UTC, date, datetime, time
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from flowmate.ai.provider import SnoozeTimeProvider
from flowmate.ai.schemas import SnoozeTimeParseResult
from flowmate.reminders.parsing import SnoozeParsingError, SnoozeParsingService
from flowmate.reminders.preferences import parse_time, validate_timezone
from flowmate.reminders.timezone import (
    quiet_hours_end,
    resolve_local_datetime,
    tomorrow_at,
)


def test_timezone_and_time_validation() -> None:
    assert validate_timezone("Europe/Riga") == "Europe/Riga"
    assert parse_time("09:30") == time(9, 30)
    with pytest.raises(ValueError):
        validate_timezone("Not/AZone")
    with pytest.raises(ValueError):
        parse_time("25:00")


def test_dst_resolution_uses_first_fold_and_next_valid_instant() -> None:
    timezone = ZoneInfo("Europe/Riga")
    missing = resolve_local_datetime(date(2026, 3, 29), time(3, 30), timezone)
    ambiguous = resolve_local_datetime(date(2026, 10, 25), time(3, 30), timezone)

    assert missing.hour == 4
    assert missing.minute == 0
    assert ambiguous.fold == 0


def test_quiet_hours_same_day_and_crossing_midnight() -> None:
    timezone = ZoneInfo("UTC")
    same_day = quiet_hours_end(
        datetime(2026, 7, 22, 13, tzinfo=UTC),
        timezone=timezone,
        start=time(12),
        end=time(14),
    )
    overnight = quiet_hours_end(
        datetime(2026, 7, 22, 23, tzinfo=UTC),
        timezone=timezone,
        start=time(22),
        end=time(8),
    )

    assert same_day == datetime(2026, 7, 22, 14, tzinfo=UTC)
    assert overnight == datetime(2026, 7, 23, 8, tzinfo=UTC)
    assert tomorrow_at(
        datetime(2026, 7, 22, 23, tzinfo=UTC),
        timezone=timezone,
        local_time=time(9),
    ) == datetime(2026, 7, 23, 9, tzinfo=UTC)


@pytest.mark.asyncio
async def test_snooze_parser_prefers_exact_and_uses_strict_ai_fallback() -> None:
    now = datetime(2026, 7, 22, 10, tzinfo=UTC)
    exact = SnoozeParsingService(None, timeout_seconds=5)
    assert await exact.parse(
        "2026-07-23 09:00", timezone=ZoneInfo("UTC"), now=now
    ) == datetime(2026, 7, 23, 9, tzinfo=UTC)

    provider = MagicMock()
    provider.parse_snooze_time = AsyncMock(
        return_value=SnoozeTimeParseResult(
            original_phrase="завтра после обеда",
            normalized_value=datetime(2026, 7, 23, 14, tzinfo=UTC),
            confidence=0.9,
            ambiguities=[],
        )
    )
    service = SnoozeParsingService(
        cast(SnoozeTimeProvider, provider), timeout_seconds=5
    )
    result = await service.parse(
        "завтра после обеда", timezone=ZoneInfo("UTC"), now=now
    )

    assert result == datetime(2026, 7, 23, 14, tzinfo=UTC)
    provider.parse_snooze_time.assert_awaited_once()


@pytest.mark.asyncio
async def test_snooze_parser_rejects_past_and_unavailable_natural_language() -> None:
    service = SnoozeParsingService(None, timeout_seconds=5)
    now = datetime(2026, 7, 22, 10, tzinfo=UTC)
    with pytest.raises(SnoozeParsingError):
        await service.parse("2026-07-21 09:00", timezone=ZoneInfo("UTC"), now=now)
    with pytest.raises(SnoozeParsingError):
        await service.parse("когда-нибудь", timezone=ZoneInfo("UTC"), now=now)
