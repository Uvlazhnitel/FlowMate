import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowmate.db.session import session_scope
from flowmate.reminders.digests import (
    ensure_daily_digest_reminders,
    prepare_digest_message,
)
from flowmate.reminders.enums import ReminderType
from flowmate.reminders.notifications import (
    NotificationService,
    PermanentNotificationError,
    TemporaryNotificationError,
    build_reminder_notification,
)
from flowmate.reminders.preferences import (
    NotificationDefaults,
    defer_due_reminders_for_quiet_hours,
)
from flowmate.reminders.service import (
    ClaimedReminder,
    cancel_claimed_reminder,
    claim_due_reminders,
    get_claimed_reminder_delivery,
    mark_reminder_delivery_failure,
    mark_reminder_delivery_started,
    mark_reminder_delivery_unknown,
    mark_reminder_sent,
)

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class ReminderProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        notification_service: NotificationService,
        *,
        batch_size: int,
        max_attempts: int,
        retry_delay: timedelta,
        processing_timeout: timedelta,
        delivery_timeout_seconds: int,
        notification_defaults: NotificationDefaults | None = None,
        timezone: ZoneInfo | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._notification_service = notification_service
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay
        self._processing_timeout = processing_timeout
        self._delivery_timeout_seconds = delivery_timeout_seconds
        self._notification_defaults = notification_defaults or NotificationDefaults(
            timezone=(timezone or ZoneInfo("UTC")).key,
            morning_digest_time=datetime.min.time(),
            evening_digest_time=datetime.min.time(),
            quiet_hours_start=datetime.min.time(),
            quiet_hours_end=datetime.max.time().replace(microsecond=0),
            snooze_minutes=60,
        )
        self._clock = clock

    async def process_due_reminders(self) -> None:
        now = self._clock()
        async with session_scope(self._session_factory) as session:
            await ensure_daily_digest_reminders(session, now=now)
            await defer_due_reminders_for_quiet_hours(
                session,
                now=now,
                defaults=self._notification_defaults,
                limit=self._batch_size * 4,
            )
            claims = await claim_due_reminders(
                session,
                now=now,
                limit=self._batch_size,
                max_attempts=self._max_attempts,
                processing_timeout=self._processing_timeout,
            )
        for claim in claims:
            await self._process_claim(claim)

    async def _process_claim(self, claim: ClaimedReminder) -> None:
        async with session_scope(self._session_factory) as session:
            delivery = await get_claimed_reminder_delivery(
                session,
                claim,
                now=self._clock(),
                notification_defaults=self._notification_defaults,
            )
        if delivery is None:
            return
        if delivery.reminder_type in {
            ReminderType.MORNING_DIGEST,
            ReminderType.EVENING_DIGEST,
        }:
            if delivery.user_id is None:
                return
            async with session_scope(self._session_factory) as session:
                message = await prepare_digest_message(
                    session,
                    delivery.user_id,
                    delivery.reminder_type,
                    now=self._clock(),
                    defaults=self._notification_defaults,
                )
                if message is None:
                    await cancel_claimed_reminder(
                        session,
                        claim,
                        now=self._clock(),
                        reason="empty_digest",
                    )
                    return
            delivery = replace(delivery, message=message)
        try:
            notification = build_reminder_notification(
                delivery,
                timezone=ZoneInfo(delivery.timezone),
                now=self._clock(),
            )
            async with session_scope(self._session_factory) as session:
                started = await mark_reminder_delivery_started(
                    session, claim, now=self._clock()
                )
            if not started:
                return
            async with asyncio.timeout(self._delivery_timeout_seconds):
                await self._notification_service.send(notification)
        except PermanentNotificationError as error:
            await self._mark_failure(claim, error.code, permanent=True)
            logger.warning(
                "reminder_delivery_failed reminder_id=%s category=%s permanent=true",
                claim.id,
                error.code,
            )
            return
        except TemporaryNotificationError as error:
            if error.retry_after_seconds is None:
                await self._mark_unknown(claim, error.code)
                logger.warning(
                    "reminder_delivery_unknown reminder_id=%s category=%s",
                    claim.id,
                    error.code,
                )
                return
            retry_delay = self._retry_delay
            if error.retry_after_seconds is not None:
                retry_delay = max(
                    retry_delay,
                    timedelta(seconds=error.retry_after_seconds),
                )
            await self._mark_failure(
                claim,
                error.code,
                permanent=False,
                retry_delay=retry_delay,
            )
            logger.warning(
                "reminder_delivery_failed reminder_id=%s category=%s permanent=false",
                claim.id,
                error.code,
            )
            return
        except TimeoutError:
            await self._mark_unknown(claim, "delivery_timeout")
            logger.warning(
                "reminder_delivery_unknown reminder_id=%s category=delivery_timeout",
                claim.id,
            )
            return
        async with session_scope(self._session_factory) as session:
            marked = await mark_reminder_sent(session, claim, now=self._clock())
        if marked:
            logger.info("reminder_delivered reminder_id=%s", claim.id)

    async def _mark_failure(
        self,
        claim: ClaimedReminder,
        error_code: str,
        *,
        permanent: bool,
        retry_delay: timedelta | None = None,
    ) -> None:
        async with session_scope(self._session_factory) as session:
            await mark_reminder_delivery_failure(
                session,
                claim,
                now=self._clock(),
                error_code=error_code,
                permanent=permanent,
                max_attempts=self._max_attempts,
                retry_delay=retry_delay or self._retry_delay,
            )

    async def _mark_unknown(self, claim: ClaimedReminder, error_code: str) -> None:
        async with session_scope(self._session_factory) as session:
            await mark_reminder_delivery_unknown(
                session,
                claim,
                now=self._clock(),
                error_code=error_code,
            )
