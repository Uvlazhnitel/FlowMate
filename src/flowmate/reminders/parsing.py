# ruff: noqa: RUF001
import asyncio
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flowmate.ai.errors import AIError, AIInvalidResponseError, AITimeoutError
from flowmate.ai.prompt_versions import SNOOZE_PROMPT_VERSION
from flowmate.ai.provider import SnoozeTimeProvider
from flowmate.ai.schemas import SnoozeTimeParseResult
from flowmate.reminders.timezone import resolve_local_datetime


class SnoozeParsingError(ValueError):
    """A custom snooze value could not be resolved safely."""


class SnoozeParsingService:
    def __init__(
        self,
        provider: SnoozeTimeProvider | None,
        *,
        timeout_seconds: int,
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    async def parse(
        self,
        value: str,
        *,
        timezone: ZoneInfo,
        now: datetime,
        default_time: time = time(9),
    ) -> datetime:
        normalized = value.strip()
        if not normalized:
            raise SnoozeParsingError("snooze value must not be empty")
        local = self.parse_deterministic(
            normalized,
            timezone=timezone,
            now=now,
            default_time=default_time,
        )
        if local is not None:
            if local <= now:
                raise SnoozeParsingError("snooze value must be in the future")
            return local
        if self._provider is None:
            raise SnoozeParsingError("natural-language snooze parsing is unavailable")
        prompt = (
            f"Prompt version: {SNOOZE_PROMPT_VERSION}. "
            "Resolve exactly one future reminder time. Return the configured strict "
            "schema only. Do not infer a value when the phrase is materially "
            "ambiguous. "
            f"Current datetime: {now.astimezone(timezone).isoformat()}. "
            f"Timezone: {timezone.key}."
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                parsed = await self._provider.parse_snooze_time(
                    system_prompt=prompt,
                    user_text=normalized,
                )
        except TimeoutError as error:
            raise AITimeoutError("snooze parsing timed out") from error
        except AIError:
            raise
        if not isinstance(parsed, SnoozeTimeParseResult):
            raise AIInvalidResponseError("invalid snooze parse result")
        result = parsed.normalized_value
        if parsed.ambiguities or parsed.confidence < 0.8 or result <= now:
            raise SnoozeParsingError("snooze time is ambiguous or not in the future")
        return result

    @staticmethod
    def parse_deterministic(
        value: str,
        *,
        timezone: ZoneInfo,
        now: datetime,
        default_time: time,
    ) -> datetime | None:
        normalized = " ".join(value.casefold().replace("ё", "е").split())
        for pattern in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
            try:
                parsed = datetime.strptime(normalized, pattern)
            except ValueError:
                continue
            return resolve_local_datetime(parsed.date(), parsed.time(), timezone)

        if normalized == "через час":
            return now + timedelta(hours=1)

        relative = re.fullmatch(
            r"через\s+(?P<count>\d+|пол|один|одну|два|две)\s+"
            r"(?P<unit>минут(?:у|ы)?|час(?:а|ов)?|недел(?:ю|и|ь))",
            normalized,
        )
        if normalized == "через неделю":
            local_now = now.astimezone(timezone)
            return resolve_local_datetime(
                local_now.date() + timedelta(days=7),
                default_time,
                timezone,
            )
        if relative is not None:
            raw_count = relative.group("count")
            count = float(
                {
                    "пол": 0.5,
                    "один": 1,
                    "одну": 1,
                    "два": 2,
                    "две": 2,
                }.get(raw_count, float(raw_count) if raw_count.isdigit() else 1)
            )
            unit = relative.group("unit")
            if unit.startswith("минут"):
                return now + timedelta(minutes=count)
            if unit.startswith("час"):
                return now + timedelta(hours=count)
            if count.is_integer():
                local_now = now.astimezone(timezone)
                return resolve_local_datetime(
                    local_now.date() + timedelta(days=7 * int(count)),
                    default_time,
                    timezone,
                )
            return now + timedelta(weeks=count)

        clock_match = re.search(r"(?:\s+в)?\s*(\d{1,2}):(\d{2})$", normalized)
        explicit_clock: time | None = None
        date_phrase = normalized
        if clock_match is not None:
            try:
                explicit_clock = time(
                    int(clock_match.group(1)), int(clock_match.group(2))
                )
            except ValueError:
                return None
            date_phrase = normalized[: clock_match.start()].strip()

        day_part_match = re.search(
            r"(?:\s+)(утром|днем|после обеда|вечером)$",
            date_phrase,
        )
        day_part_time: time | None = None
        if day_part_match is not None:
            day_part_time = {
                "утром": time(9),
                "днем": time(14),
                "после обеда": time(15),
                "вечером": time(19),
            }[day_part_match.group(1)]
            date_phrase = date_phrase[: day_part_match.start()].strip()

        local_now = now.astimezone(timezone)
        target_time = explicit_clock or day_part_time or default_time
        if date_phrase == "завтра":
            return resolve_local_datetime(
                local_now.date() + timedelta(days=1),
                target_time,
                timezone,
            )

        weekdays = {
            "понедельник": 0,
            "вторник": 1,
            "среду": 2,
            "четверг": 3,
            "пятницу": 4,
            "субботу": 5,
            "воскресенье": 6,
        }
        weekday_match = re.fullmatch(
            r"в\s+(?:(следующ(?:ий|ую|ее))\s+)?(" + "|".join(weekdays) + r")",
            date_phrase,
        )
        if weekday_match is not None:
            target_weekday = weekdays[weekday_match.group(2)]
            days = (target_weekday - local_now.weekday()) % 7
            if days == 0:
                days = 7
            if weekday_match.group(1) and days < 7:
                days += 7
            return resolve_local_datetime(
                local_now.date() + timedelta(days=days),
                target_time,
                timezone,
            )

        months = {
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
        absolute = re.fullmatch(
            r"(?P<day>\d{1,2})\s+(?P<month>" + "|".join(months) + r")"
            r"(?:\s+(?P<year>20\d{2}))?",
            date_phrase,
        )
        if absolute is not None:
            year = int(absolute.group("year") or local_now.year)
            try:
                target_date = date(
                    year,
                    months[absolute.group("month")],
                    int(absolute.group("day")),
                )
                if absolute.group("year") is None and target_date < local_now.date():
                    target_date = target_date.replace(year=year + 1)
            except ValueError:
                return None
            return resolve_local_datetime(target_date, target_time, timezone)

        for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                parsed_date = datetime.strptime(date_phrase, pattern).date()
            except ValueError:
                continue
            return resolve_local_datetime(parsed_date, target_time, timezone)
        return None
