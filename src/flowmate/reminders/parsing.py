import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from flowmate.ai.errors import AIError, AIInvalidResponseError, AITimeoutError
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
    ) -> datetime:
        normalized = value.strip()
        if not normalized:
            raise SnoozeParsingError("snooze value must not be empty")
        local = self._parse_exact(normalized, timezone)
        if local is not None:
            if local <= now:
                raise SnoozeParsingError("snooze value must be in the future")
            return local
        if self._provider is None:
            raise SnoozeParsingError("natural-language snooze parsing is unavailable")
        prompt = (
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
    def _parse_exact(value: str, timezone: ZoneInfo) -> datetime | None:
        for pattern in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
            try:
                parsed = datetime.strptime(value, pattern)
            except ValueError:
                continue
            return resolve_local_datetime(parsed.date(), parsed.time(), timezone)
        return None
