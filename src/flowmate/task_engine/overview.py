from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.reminders.preferences import EffectiveNotificationPreferences
from flowmate.task_engine.operational import (
    PageResult,
    WorkItemCard,
    list_overview_today_items,
    list_overview_tomorrow_items,
)
from flowmate.task_engine.remaining import list_inbox, work_item_inbox_reasons

OVERVIEW_LIMIT = 8


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
        }
    if kind == "work_item":
        card = entry["item"]
        return {
            "id": card.id,
            "kind": kind,
            "title": card.title,
            "excerpt": card.description or "",
            "status": card.status,
            "reasons": entry.get("reasons") or [],
            "occurred_at": card.updated_at,
            "item_count": 1,
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
    }


def _column(page: PageResult, items: list[dict[str, object]]) -> dict[str, object]:
    total = page.total if page.total is not None else len(page.items)
    return {"items": items, "total": total, "has_more": page.has_more}


async def overview_snapshot(
    session: AsyncSession,
    user_id: UUID,
    *,
    now: datetime,
    preferences: EffectiveNotificationPreferences,
    low_confidence_threshold: float,
) -> dict[str, object]:
    today = await list_overview_today_items(
        session,
        user_id,
        now=now,
        preferences=preferences,
        limit=OVERVIEW_LIMIT,
    )
    tomorrow = await list_overview_tomorrow_items(
        session,
        user_id,
        now=now,
        preferences=preferences,
        limit=OVERVIEW_LIMIT,
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
    )
    return {
        "today": _column(today, [_work_item_preview(item) for item in today.items]),
        "tomorrow": _column(
            tomorrow, [_work_item_preview(item) for item in tomorrow.items]
        ),
        "inbox": _column(inbox, [_inbox_preview(item) for item in inbox.items]),
    }
