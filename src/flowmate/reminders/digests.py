# ruff: noqa: RUF001
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from flowmate.db.models import (
    Reminder,
    User,
    UserNotificationPreferences,
    WorkItem,
    WorkItemEvent,
)
from flowmate.reminders.enums import ReminderStatus, ReminderType
from flowmate.reminders.preferences import (
    EffectiveNotificationPreferences,
    NotificationDefaults,
    effective_preferences,
)
from flowmate.reminders.sync import ReminderPolicy, sync_work_item_reminders
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.enums import WorkItemPriority, WorkItemStatus, WorkItemType
from flowmate.task_engine.queries import OPEN_STATUSES
from flowmate.workspaces import WORKSPACE_LABELS, WORKSPACE_VALUES, Workspace

CANONICAL_DIGEST_WORKSPACE = Workspace.PERSONAL.value
DIGEST_ITEM_LIMIT = 5
DIGEST_TITLE_LIMIT = 96


@dataclass(frozen=True, slots=True)
class DigestItem:
    id: UUID
    title: str
    item_type: str
    status: str
    effective_at: datetime | None
    category: int


@dataclass(frozen=True, slots=True)
class WorkspaceDigestSnapshot:
    workspace: str
    items: tuple[DigestItem, ...] = ()
    total: int = 0

    @property
    def empty(self) -> bool:
        return self.total == 0


@dataclass(frozen=True, slots=True)
class DigestSnapshot:
    workspaces: tuple[WorkspaceDigestSnapshot, ...]

    @property
    def empty(self) -> bool:
        return all(snapshot.empty for snapshot in self.workspaces)


def _priority_rank_sql() -> ColumnElement[int]:
    return case(
        (WorkItem.priority == WorkItemPriority.URGENT.value, 0),
        (WorkItem.priority == WorkItemPriority.HIGH.value, 1),
        (WorkItem.priority == WorkItemPriority.NORMAL.value, 2),
        (WorkItem.priority == WorkItemPriority.LOW.value, 3),
        else_=2,
    )


def _effective_at_sql() -> ColumnElement[datetime]:
    return case(
        (
            WorkItem.type == WorkItemType.FOLLOW_UP.value,
            WorkItem.next_follow_up_at,
        ),
        else_=WorkItem.due_at,
    )


def _digest_category_sql(
    reminder_type: ReminderType,
    *,
    now: datetime,
    start: datetime,
    end: datetime,
) -> ColumnElement[int]:
    ordinary = WorkItem.type.not_in(
        [
            WorkItemType.FOLLOW_UP.value,
            WorkItemType.WAITING.value,
            WorkItemType.QUESTION.value,
        ]
    )
    if reminder_type is ReminderType.MORNING_DIGEST:
        return case(
            (ordinary & (WorkItem.due_at < now), 0),
            (
                (WorkItem.type == WorkItemType.FOLLOW_UP.value)
                & (WorkItem.next_follow_up_at < end),
                1,
            ),
            (
                (WorkItem.type == WorkItemType.WAITING.value) & (WorkItem.due_at < now),
                2,
            ),
            (
                ordinary & (WorkItem.due_at >= now) & (WorkItem.due_at < end),
                3,
            ),
            (WorkItem.type == WorkItemType.QUESTION.value, 4),
            (WorkItem.status == WorkItemStatus.INBOX.value, 5),
            else_=6,
        )
    return case(
        (
            WorkItem.type.not_in(
                [WorkItemType.FOLLOW_UP.value, WorkItemType.WAITING.value]
            )
            & (WorkItem.due_at >= start)
            & (WorkItem.due_at < end),
            0,
        ),
        (
            (WorkItem.type == WorkItemType.FOLLOW_UP.value)
            & (WorkItem.next_follow_up_at < end),
            1,
        ),
        (
            (WorkItem.type == WorkItemType.WAITING.value) & (WorkItem.due_at < end),
            2,
        ),
        else_=3,
    )


async def _build_workspace_digest_snapshot(
    session: AsyncSession,
    user_id: UUID,
    reminder_type: ReminderType,
    *,
    workspace: str,
    now: datetime,
    start: datetime,
    end: datetime,
) -> WorkspaceDigestSnapshot:
    category = _digest_category_sql(
        reminder_type,
        now=now,
        start=start,
        end=end,
    )
    max_category = 6 if reminder_type is ReminderType.MORNING_DIGEST else 3
    conditions = (
        WorkItem.user_id == user_id,
        WorkItem.workspace == workspace,
        WorkItem.status.in_(OPEN_STATUSES),
        category < max_category,
    )
    total = int(
        (
            await session.scalar(
                select(func.count(WorkItem.id))
                .where(*conditions)
                .execution_options(include_all_workspaces=True)
            )
        )
        or 0
    )
    if total == 0:
        return WorkspaceDigestSnapshot(workspace=workspace)
    inbox_recency = case(
        (category == 5, WorkItem.created_at),
        else_=None,
    )
    rows = list(
        await session.execute(
            select(WorkItem, category.label("digest_category"))
            .where(*conditions)
            .order_by(
                category,
                _priority_rank_sql(),
                inbox_recency.desc().nulls_last(),
                _effective_at_sql().asc().nulls_last(),
                WorkItem.id,
            )
            .limit(DIGEST_ITEM_LIMIT)
            .execution_options(include_all_workspaces=True)
        )
    )
    items = tuple(
        DigestItem(
            id=item.id,
            title=item.title,
            item_type=item.type,
            status=item.status,
            effective_at=(
                item.next_follow_up_at
                if item.type == WorkItemType.FOLLOW_UP.value
                else item.due_at
            ),
            category=int(index),
        )
        for item, index in rows
    )
    return WorkspaceDigestSnapshot(workspace=workspace, items=items, total=total)


async def build_digest_snapshot(
    session: AsyncSession,
    user_id: UUID,
    reminder_type: ReminderType,
    *,
    now: datetime,
    preferences: EffectiveNotificationPreferences,
) -> DigestSnapshot:
    timezone = preferences.zoneinfo
    local_now = now.astimezone(timezone)
    start = resolve_local_datetime(
        local_now.date(),
        local_now.replace(hour=0, minute=0, second=0, microsecond=0).time(),
        timezone,
    ).astimezone(UTC)
    end = resolve_local_datetime(
        local_now.date() + timedelta(days=1),
        local_now.replace(hour=0, minute=0, second=0, microsecond=0).time(),
        timezone,
    ).astimezone(UTC)
    return DigestSnapshot(
        workspaces=tuple(
            [
                await _build_workspace_digest_snapshot(
                    session,
                    user_id,
                    reminder_type,
                    workspace=workspace,
                    now=now,
                    start=start,
                    end=end,
                )
                for workspace in WORKSPACE_VALUES
            ]
        )
    )


def format_digest_message(
    reminder_type: ReminderType,
    snapshot: DigestSnapshot,
    *,
    timezone: ZoneInfo,
    now: datetime,
) -> str:
    local_now = now.astimezone(timezone)
    title = (
        "☀️ Доброе утро"
        if reminder_type is ReminderType.MORNING_DIGEST
        else "🌙 Вечерний обзор"
    )
    sections: list[str] = []
    workspace_icons = {
        Workspace.PERSONAL.value: "🏠",
        Workspace.WORK.value: "💼",
    }
    for workspace_snapshot in snapshot.workspaces:
        if workspace_snapshot.empty:
            continue
        lines = [
            f"{workspace_icons[workspace_snapshot.workspace]} "
            f"{WORKSPACE_LABELS[workspace_snapshot.workspace]}"
        ]
        for item in workspace_snapshot.items:
            normalized = " ".join(item.title.split())
            display_title = (
                normalized
                if len(normalized) <= DIGEST_TITLE_LIMIT
                else f"{normalized[: DIGEST_TITLE_LIMIT - 1].rstrip()}…"
            )
            if item.item_type == WorkItemType.FOLLOW_UP.value:
                icon = "🔁"
            elif item.item_type == WorkItemType.WAITING.value:
                icon = "⏳"
            elif item.item_type == WorkItemType.QUESTION.value:
                icon = "❓"
            elif item.category == 5:
                icon = "📥"
            elif item.effective_at is not None and item.effective_at < now:
                icon = "🔴"
            else:
                icon = "🟠"
            suffix = ""
            if item.effective_at is not None:
                local_effective = item.effective_at.astimezone(timezone)
                formatted = (
                    f"{local_effective:%H:%M}"
                    if local_effective.date() == local_now.date()
                    else f"{local_effective:%d.%m, %H:%M}"
                )
                suffix = f" · {formatted}"
            lines.append(f"{icon} {display_title}{suffix}")
        remaining = workspace_snapshot.total - len(workspace_snapshot.items)
        if remaining > 0:
            lines.append(f"+ ещё {remaining}")
        sections.append("\n".join(lines))
    return "\n\n".join([title, *(sections or ["На сегодня всё спокойно."])])


async def ensure_daily_digest_reminders(
    session: AsyncSession,
    *,
    now: datetime,
) -> int:
    rows = await session.execute(
        select(UserNotificationPreferences)
        .join(User, User.id == UserNotificationPreferences.user_id)
        .where(
            User.is_active.is_(True),
            or_(
                UserNotificationPreferences.morning_digest_enabled.is_(True),
                UserNotificationPreferences.evening_digest_enabled.is_(True),
            ),
        )
    )
    created = 0
    for (preferences,) in rows:
        timezone = preferences.timezone
        preferences_zone = effective_preferences(
            preferences,
            NotificationDefaults(
                timezone=timezone,
                morning_digest_time=preferences.morning_digest_time,
                evening_digest_time=preferences.evening_digest_time,
                quiet_hours_start=preferences.quiet_hours_start,
                quiet_hours_end=preferences.quiet_hours_end,
                snooze_minutes=preferences.default_snooze_minutes,
            ),
        )
        local_date = now.astimezone(preferences_zone.zoneinfo).date()
        definitions = (
            (
                ReminderType.MORNING_DIGEST,
                preferences.morning_digest_enabled,
                preferences.morning_digest_time,
            ),
            (
                ReminderType.EVENING_DIGEST,
                preferences.evening_digest_enabled,
                preferences.evening_digest_time,
            ),
        )
        for reminder_type, enabled, digest_time in definitions:
            if not enabled:
                continue
            scheduled_at = resolve_local_datetime(
                local_date, digest_time, preferences_zone.zoneinfo
            ).astimezone(UTC)
            statement = (
                insert(Reminder)
                .values(
                    id=uuid4(),
                    user_id=preferences.user_id,
                    workspace=CANONICAL_DIGEST_WORKSPACE,
                    type=reminder_type.value,
                    scheduled_at=scheduled_at,
                    schedule_kind="manual",
                    status=ReminderStatus.PENDING.value,
                    deduplication_key=f"digest:{reminder_type.value}:{local_date}",
                    digest_local_date=local_date,
                    schedule_timezone=timezone,
                )
                .on_conflict_do_update(
                    constraint=("uq_reminders_user_workspace_digest_local_date"),
                    set_={
                        "scheduled_at": scheduled_at,
                        "schedule_timezone": timezone,
                        "deduplication_key": (
                            f"digest:{reminder_type.value}:{local_date}"
                        ),
                        "status": case(
                            (Reminder.sent_at.is_not(None), Reminder.status),
                            else_=ReminderStatus.PENDING.value,
                        ),
                        "cancelled_at": case(
                            (Reminder.sent_at.is_not(None), Reminder.cancelled_at),
                            else_=None,
                        ),
                    },
                )
                .returning(Reminder.id)
            )
            if (await session.scalar(statement)) is not None:
                created += 1
            await session.execute(
                update(Reminder)
                .where(
                    Reminder.user_id == preferences.user_id,
                    Reminder.workspace == Workspace.WORK.value,
                    Reminder.type == reminder_type.value,
                    Reminder.digest_local_date == local_date,
                    Reminder.sent_at.is_(None),
                    Reminder.status.in_(
                        [
                            ReminderStatus.PENDING.value,
                            ReminderStatus.SNOOZED.value,
                            ReminderStatus.FAILED.value,
                        ]
                    ),
                )
                .values(
                    status=ReminderStatus.CANCELLED.value,
                    cancelled_at=now,
                    snoozed_until=None,
                    next_attempt_at=None,
                    processing_started_at=None,
                    processing_token=None,
                )
            )
    await session.flush()
    return created


async def prepare_digest_message(
    session: AsyncSession,
    user_id: UUID,
    reminder_type: ReminderType,
    *,
    now: datetime,
    defaults: NotificationDefaults,
) -> str | None:
    from flowmate.reminders.preferences import (
        get_effective_notification_preferences,
    )

    preferences = await get_effective_notification_preferences(
        session, user_id, defaults
    )
    enabled = (
        preferences.morning_digest_enabled
        if reminder_type is ReminderType.MORNING_DIGEST
        else preferences.evening_digest_enabled
    )
    if not enabled:
        return None
    snapshot = await build_digest_snapshot(
        session,
        user_id,
        reminder_type,
        now=now,
        preferences=preferences,
    )
    if snapshot.empty and not preferences.send_empty_digests:
        return None
    return format_digest_message(
        reminder_type,
        snapshot,
        timezone=preferences.zoneinfo,
        now=now,
    )


async def cancel_future_digests(
    session: AsyncSession,
    user_id: UUID,
    reminder_type: ReminderType,
    *,
    now: datetime,
) -> None:
    values = list(
        await session.scalars(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.type == reminder_type.value,
                Reminder.status.in_(
                    [ReminderStatus.PENDING.value, ReminderStatus.SNOOZED.value]
                ),
                Reminder.sent_at.is_(None),
            )
            .with_for_update()
            .execution_options(include_all_workspaces=True)
        )
    )
    for value in values:
        value.status = ReminderStatus.CANCELLED.value
        value.cancelled_at = now
        value.snoozed_until = None


async def list_digest_reschedule_items(
    session: AsyncSession,
    user_id: UUID,
    *,
    local_date: date,
    preferences: EffectiveNotificationPreferences,
    workspace: str,
) -> list[WorkItem]:
    timezone = preferences.zoneinfo
    midnight = resolve_local_datetime(local_date, datetime.min.time(), timezone)
    end = resolve_local_datetime(
        local_date + timedelta(days=1), datetime.min.time(), timezone
    )
    return list(
        await session.scalars(
            select(WorkItem)
            .where(
                WorkItem.user_id == user_id,
                WorkItem.workspace == workspace,
                WorkItem.status.in_(OPEN_STATUSES),
                or_(
                    and_(
                        WorkItem.type.in_(
                            [
                                WorkItemType.TASK.value,
                                WorkItemType.QUESTION.value,
                                WorkItemType.DECISION.value,
                                WorkItemType.AGENDA_ITEM.value,
                            ]
                        ),
                        WorkItem.due_at >= midnight,
                        WorkItem.due_at < end,
                    ),
                    and_(
                        WorkItem.type == WorkItemType.FOLLOW_UP.value,
                        WorkItem.next_follow_up_at < end,
                    ),
                ),
            )
            .order_by(WorkItem.created_at)
            .with_for_update()
            .execution_options(include_all_workspaces=True)
        )
    )


async def move_digest_items_to_tomorrow(
    session: AsyncSession,
    user_id: UUID,
    *,
    local_date: date,
    telegram_update_id: int,
    preferences: EffectiveNotificationPreferences,
    workspace: str,
    reminder_policy: ReminderPolicy | None = None,
) -> int:
    from flowmate.task_engine.management import event_for_update

    if await event_for_update(session, user_id, telegram_update_id) is not None:
        return 0
    items = await list_digest_reschedule_items(
        session,
        user_id,
        local_date=local_date,
        preferences=preferences,
        workspace=workspace,
    )
    timezone = preferences.zoneinfo
    for index, item in enumerate(items):
        field = (
            "next_follow_up_at"
            if item.type == WorkItemType.FOLLOW_UP.value
            else "due_at"
        )
        previous = getattr(item, field)
        if previous is None:
            continue
        localized = previous.astimezone(timezone)
        new_value = resolve_local_datetime(
            localized.date() + timedelta(days=1),
            localized.time().replace(tzinfo=None),
            timezone,
        )
        setattr(item, field, new_value)
        session.add(
            WorkItemEvent(
                user_id=user_id,
                work_item_id=item.id,
                event_type="rescheduled",
                telegram_update_id=telegram_update_id if index == 0 else None,
                payload={
                    "field": field,
                    "previous": previous.isoformat(),
                    "new": new_value.isoformat(),
                    "source": "evening_digest",
                },
            )
        )
        await sync_work_item_reminders(
            session,
            item,
            policy=reminder_policy,
            allow_final_replacement=True,
        )
    await session.flush()
    return len(items)
