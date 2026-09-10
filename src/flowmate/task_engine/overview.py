from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import WorkItem
from flowmate.reminders.preferences import EffectiveNotificationPreferences
from flowmate.task_engine.enums import WorkItemStatus, WorkItemType
from flowmate.task_engine.operational import (
    PageResult,
    WorkItemCard,
    build_work_item_cards,
    effective_date_sql,
    list_overview_today_items,
    list_overview_tomorrow_items,
    local_day_bounds,
)
from flowmate.task_engine.queries import top_level_work_item_filter
from flowmate.task_engine.remaining import list_inbox, work_item_inbox_reasons
from flowmate.workspaces import WorkspaceReadScope, workspace_counts

OVERVIEW_LIMIT = 8
OverviewBucket = Literal["today", "tomorrow", "inbox"]


def _work_item_preview(card: WorkItemCard) -> dict[str, object]:
    return {
        "item": card,
        "needs_inbox": bool(work_item_inbox_reasons(card)),
    }


def _inbox_preview(entry: dict[str, Any]) -> dict[str, object]:
    kind = str(entry["kind"])
    if kind == "draft":
        items = entry.get("items") or []
        first = items[0] if items else {}
        return {
            "id": entry["id"],
            "kind": kind,
            "title": first.get("title") or "Черновик AI",
            "excerpt": entry.get("source_excerpt") or "",
            "status": entry.get("status"),
            "reasons": entry.get("reasons") or [],
            "occurred_at": entry.get("updated_at"),
            "item_count": len(items),
            "workspace": entry["workspace"],
        }
    if kind == "work_item":
        card = entry["item"]
        return {
            "id": card.id,
            "kind": kind,
            "item": card,
            "title": card.title,
            "excerpt": card.description or "",
            "status": card.status,
            "reasons": entry.get("reasons") or [],
            "occurred_at": card.updated_at,
            "item_count": 1,
            "workspace": card.workspace,
        }
    return {
        "id": entry["id"],
        "kind": kind,
        "title": "Неразобранная заметка",
        "excerpt": entry.get("excerpt") or "",
        "status": "pending",
        "reasons": entry.get("reasons") or [],
        "occurred_at": entry.get("created_at"),
        "item_count": 1,
        "workspace": entry["workspace"],
    }


def _column(page: PageResult, items: list[dict[str, object]]) -> dict[str, object]:
    total = page.total if page.total is not None else len(page.items)
    return {"items": items, "total": total, "has_more": page.has_more}


async def _completed_column(
    session: AsyncSession,
    user_id: UUID,
    *,
    bucket: OverviewBucket,
    now: datetime,
    preferences: EffectiveNotificationPreferences,
    workspace_scope: WorkspaceReadScope,
    search_query: str | None,
) -> tuple[dict[str, object], dict[str, set[tuple[str, UUID]]]]:
    today_start, today_end = local_day_bounds(now, preferences)
    tomorrow_start, tomorrow_end = local_day_bounds(now, preferences, days_ahead=1)
    effective = effective_date_sql()
    conditions: list[Any] = [
        WorkItem.user_id == user_id,
        top_level_work_item_filter(),
        WorkItem.status == WorkItemStatus.DONE.value,
        WorkItem.completed_at >= today_start,
        WorkItem.completed_at < today_end,
        WorkItem.type.in_(
            (
                WorkItemType.TASK.value,
                WorkItemType.FOLLOW_UP.value,
                WorkItemType.WAITING.value,
                WorkItemType.QUESTION.value,
            )
        ),
    ]
    if bucket == "today":
        conditions.extend((effective.is_not(None), effective < today_end))
    elif bucket == "tomorrow":
        conditions.extend((effective >= tomorrow_start, effective < tomorrow_end))
    else:
        conditions.append(effective.is_(None))
    if search_query:
        escaped = (
            search_query.strip()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        conditions.append(
            WorkItem.title.ilike(pattern, escape="\\")
            | WorkItem.description.ilike(pattern, escape="\\")
        )
    all_rows = list(
        await session.scalars(
            select(WorkItem)
            .where(*conditions)
            .execution_options(include_all_workspaces=True)
            .order_by(WorkItem.completed_at.desc(), WorkItem.id)
        )
    )
    keys: dict[str, set[tuple[str, UUID]]] = {"work": set(), "personal": set()}
    for item in all_rows:
        keys[item.workspace].add(("work_item", item.id))
    scoped = (
        all_rows
        if workspace_scope == "all"
        else [item for item in all_rows if item.workspace == workspace_scope]
    )
    cards = await build_work_item_cards(
        session, user_id, scoped[:OVERVIEW_LIMIT], now=now
    )
    return (
        {
            "completed_items": cards,
            "completed_total": len(scoped),
            "completed_has_more": len(scoped) > OVERVIEW_LIMIT,
        },
        keys,
    )


async def overview_snapshot(
    session: AsyncSession,
    user_id: UUID,
    *,
    now: datetime,
    preferences: EffectiveNotificationPreferences,
    low_confidence_threshold: float,
    workspace_scope: WorkspaceReadScope = "all",
    search_query: str | None = None,
) -> dict[str, object]:
    today = await list_overview_today_items(
        session,
        user_id,
        now=now,
        preferences=preferences,
        limit=OVERVIEW_LIMIT,
        workspace_scope=workspace_scope,
        search_query=search_query,
    )
    tomorrow = await list_overview_tomorrow_items(
        session,
        user_id,
        now=now,
        preferences=preferences,
        limit=OVERVIEW_LIMIT,
        workspace_scope=workspace_scope,
        search_query=search_query,
    )
    inbox = await list_inbox(
        session,
        user_id,
        now=now,
        low_confidence_threshold=low_confidence_threshold,
        kind=None,
        reason=None,
        limit=OVERVIEW_LIMIT,
        offset=0,
        workspace_scope=workspace_scope,
        search_query=search_query,
    )
    completed: dict[OverviewBucket, dict[str, object]] = {}
    completed_keys: dict[OverviewBucket, dict[str, set[tuple[str, UUID]]]] = {}
    for bucket in ("today", "tomorrow", "inbox"):
        completed[bucket], completed_keys[bucket] = await _completed_column(
            session,
            user_id,
            bucket=bucket,
            now=now,
            preferences=preferences,
            workspace_scope=workspace_scope,
            search_query=search_query,
        )
    unique: dict[str, set[tuple[str, UUID]]] = {"work": set(), "personal": set()}
    for page in (today, tomorrow, inbox):
        for workspace, keys in (page.workspace_entity_keys or {}).items():
            unique[workspace].update(keys)
    for bucket_keys in completed_keys.values():
        for workspace, keys in bucket_keys.items():
            unique[workspace].update(keys)
    counts = workspace_counts(
        work=len(unique["work"]), personal=len(unique["personal"])
    )
    return {
        "workspace_counts": counts,
        "today": {
            **_column(today, [_work_item_preview(item) for item in today.items]),
            **completed["today"],
        },
        "tomorrow": {
            **_column(tomorrow, [_work_item_preview(item) for item in tomorrow.items]),
            **completed["tomorrow"],
        },
        "inbox": {
            **_column(inbox, [_inbox_preview(item) for item in inbox.items]),
            **completed["inbox"],
        },
    }
