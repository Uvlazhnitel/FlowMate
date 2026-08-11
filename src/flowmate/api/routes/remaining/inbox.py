from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import DraftItemType, DraftReadiness
from flowmate.api.dependencies import get_session
from flowmate.auth.dependencies import PwaIdentity, require_csrf, require_pwa_session
from flowmate.core.config import Settings, get_settings
from flowmate.db.drafts import transition_draft
from flowmate.db.models import Note
from flowmate.reminders.preferences import (
    EffectiveNotificationPreferences,
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.conversion import (
    DraftConversionError,
    DraftConversionService,
)
from flowmate.task_engine.enums import WorkItemPriority
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    archive_work_item,
    bind_client_action,
)
from flowmate.task_engine.operational import PageResult
from flowmate.task_engine.remaining import (
    DraftItemEdit,
    InboxDeletionConflictError,
    InboxDeletionNotFoundError,
    delete_inbox_draft,
    delete_standalone_inbox_note,
    edit_draft_item,
    get_owned_draft,
    list_inbox,
    serialize_draft,
)

router = APIRouter()


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftItemEditRequest(StrictRequest):
    expected_revision: int = Field(ge=0)
    item_type: DraftItemType
    title: str = Field(min_length=1, max_length=10_000)
    description: str | None = Field(default=None, max_length=20_000)
    priority: WorkItemPriority = WorkItemPriority.NORMAL
    topic_id: UUID | None = None
    person_ids: list[UUID] = Field(default_factory=list, max_length=50)
    local_date: date | None = None
    local_time: time | None = None


class DraftActionRequest(StrictRequest):
    action: Literal["confirm", "save_as_note", "cancel", "recover", "delete"]
    expected_revision: int = Field(ge=0)
    accept_uncertainty: bool = False


class NoteActionRequest(StrictRequest):
    action: Literal["keep", "archive", "delete"]


class BulkEntry(StrictRequest):
    kind: Literal["draft", "note", "work_item"]
    id: UUID
    expected_revision: int | None = Field(default=None, ge=0)
    client_action_id: UUID | None = None


class BulkActionRequest(StrictRequest):
    action: Literal["cancel", "archive", "keep", "delete"]
    entries: list[BulkEntry] = Field(min_length=1, max_length=50)


def _page_payload(page: PageResult) -> dict[str, object]:
    return {
        "items": page.items,
        "limit": page.limit,
        "offset": page.offset,
        "has_more": page.has_more,
    }


def _now() -> datetime:
    return datetime.now(UTC)


async def _preferences(
    session: AsyncSession, identity: PwaIdentity, settings: Settings
) -> EffectiveNotificationPreferences:
    return await get_effective_notification_preferences(
        session,
        identity.user.id,
        NotificationDefaults.from_settings(settings),
    )


@router.get("/inbox")
async def inbox(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    kind: Literal["draft", "work_item", "note"] | None = None,
    reason: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    return _page_payload(
        await list_inbox(
            session,
            identity.user.id,
            now=_now(),
            low_confidence_threshold=settings.ai_high_confidence_threshold,
            kind=kind,
            reason=reason,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/inbox/drafts/{draft_id}")
async def inbox_draft(
    draft_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
) -> dict[str, object]:
    draft = await get_owned_draft(session, identity.user.id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    note = await session.scalar(
        select(Note).where(
            Note.id == draft.source_note_id, Note.user_id == identity.user.id
        )
    )
    return await serialize_draft(
        session,
        draft,
        source_content=(note.content or "") if note is not None else "",
    )


@router.patch("/inbox/drafts/{draft_id}/items/{item_id}")
async def update_draft_item(
    draft_id: UUID,
    item_id: UUID,
    payload: DraftItemEditRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    preferences = await _preferences(session, identity, settings)
    if payload.local_time is not None and payload.local_date is None:
        raise HTTPException(status_code=422, detail="Date is required for local time")
    due_at = (
        resolve_local_datetime(
            payload.local_date,
            (payload.local_time or time(9, 0)).replace(tzinfo=None),
            ZoneInfo(preferences.timezone),
        ).astimezone(UTC)
        if payload.local_date is not None
        else None
    )
    try:
        draft = await edit_draft_item(
            session,
            identity.user.id,
            draft_id,
            item_id,
            DraftItemEdit(
                item_type=payload.item_type,
                title=payload.title,
                description=payload.description,
                priority=payload.priority,
                topic_id=payload.topic_id,
                person_ids=tuple(payload.person_ids),
                due_at=due_at,
            ),
            expected_revision=payload.expected_revision,
            high_threshold=settings.ai_high_confidence_threshold,
            clarification_threshold=settings.ai_clarification_confidence_threshold,
            ttl_hours=settings.draft_ttl_hours,
            now=_now(),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    note = await session.get(Note, draft.source_note_id)
    return await serialize_draft(
        session,
        draft,
        source_content=(note.content or "") if note is not None else "",
    )


@router.post("/inbox/drafts/{draft_id}/actions")
async def draft_action(
    draft_id: UUID,
    payload: DraftActionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    if payload.action == "delete":
        try:
            deleted = await delete_inbox_draft(
                session,
                identity.user.id,
                draft_id,
                expected_revision=payload.expected_revision,
            )
        except InboxDeletionNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except InboxDeletionConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "status": "deleted",
            "id": deleted.note_id,
            "draft_id": deleted.draft_id,
        }
    draft = await get_owned_draft(session, identity.user.id, draft_id, for_update=True)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    revision = int(draft.updated_at.astimezone(UTC).timestamp() * 1_000_000)
    if revision != payload.expected_revision:
        raise HTTPException(status_code=409, detail="Draft is stale")
    note = await session.scalar(
        select(Note)
        .where(Note.id == draft.source_note_id, Note.user_id == identity.user.id)
        .with_for_update()
    )
    if note is None:
        raise HTTPException(status_code=409, detail="Source note is unavailable")
    if payload.action == "confirm":
        uncertain = any(
            item.readiness != DraftReadiness.READY.value
            or item.confidence < settings.ai_high_confidence_threshold
            for item in draft.items
        )
        if uncertain and not payload.accept_uncertainty:
            raise HTTPException(
                status_code=409,
                detail="Explicit uncertainty confirmation is required",
            )
        try:
            result = await DraftConversionService().convert(
                session,
                draft_id=draft.id,
                user_id=identity.user.id,
                allow_incomplete=payload.accept_uncertainty,
            )
        except DraftConversionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        note.inbox_disposition = "kept"
        return {
            "status": "confirmed",
            "work_item_ids": [item.id for item in result.work_items],
            "note_ids": [value.id for value in result.notes],
        }
    if payload.action == "recover":
        if not draft.items or draft.analysis_payload is None:
            raise HTTPException(status_code=409, detail="Draft cannot be recovered")
        if all(item.readiness == DraftReadiness.READY.value for item in draft.items):
            target = "ready"
            await transition_draft(session, draft, "ready")
        else:
            target = "needs_clarification"
            await transition_draft(session, draft, "needs_clarification")
        draft.expires_at = _now() + timedelta(hours=settings.draft_ttl_hours)
        return {"status": target}
    note.inbox_disposition = "kept" if payload.action == "save_as_note" else "archived"
    await transition_draft(session, draft, "cancelled")
    return {"status": "cancelled", "note_disposition": note.inbox_disposition}


@router.post("/inbox/notes/{note_id}/actions")
async def note_action(
    note_id: UUID,
    payload: NoteActionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    if payload.action == "delete":
        try:
            deleted = await delete_standalone_inbox_note(
                session, identity.user.id, note_id
            )
        except InboxDeletionNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except InboxDeletionConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"status": "deleted", "id": deleted.note_id}
    note = await session.scalar(
        select(Note)
        .where(Note.id == note_id, Note.user_id == identity.user.id)
        .with_for_update()
    )
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    note.inbox_disposition = "kept" if payload.action == "keep" else "archived"
    await session.flush()
    return {"id": note.id, "disposition": note.inbox_disposition}


@router.post("/inbox/bulk-actions")
async def bulk_inbox_action(
    payload: BulkActionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    allowed = {
        "cancel": {"draft"},
        "archive": {"note", "work_item"},
        "keep": {"note"},
        "delete": {"draft", "note"},
    }
    if any(entry.kind not in allowed[payload.action] for entry in payload.entries):
        raise HTTPException(status_code=422, detail="Action is not safe for selection")
    for entry in payload.entries:
        if entry.kind == "draft":
            if payload.action == "delete":
                if entry.expected_revision is None:
                    raise HTTPException(
                        status_code=422,
                        detail="Draft revision is required",
                    )
                try:
                    await delete_inbox_draft(
                        session,
                        identity.user.id,
                        entry.id,
                        expected_revision=entry.expected_revision,
                    )
                except InboxDeletionNotFoundError as error:
                    raise HTTPException(status_code=404, detail=str(error)) from error
                except InboxDeletionConflictError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                continue
            draft = await get_owned_draft(
                session, identity.user.id, entry.id, for_update=True
            )
            if draft is None:
                raise HTTPException(status_code=404, detail="Inbox item not found")
            revision = int(draft.updated_at.astimezone(UTC).timestamp() * 1_000_000)
            if entry.expected_revision is None or revision != entry.expected_revision:
                raise HTTPException(status_code=409, detail="Draft is stale")
            await transition_draft(session, draft, "cancelled")
            note = await session.get(Note, draft.source_note_id)
            if note is not None and note.user_id == identity.user.id:
                note.inbox_disposition = "archived"
        elif entry.kind == "note":
            if payload.action == "delete":
                try:
                    await delete_standalone_inbox_note(
                        session, identity.user.id, entry.id
                    )
                except InboxDeletionNotFoundError as error:
                    raise HTTPException(status_code=404, detail=str(error)) from error
                except InboxDeletionConflictError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                continue
            note = await session.scalar(
                select(Note)
                .where(Note.id == entry.id, Note.user_id == identity.user.id)
                .with_for_update()
            )
            if note is None:
                raise HTTPException(status_code=404, detail="Inbox item not found")
            note.inbox_disposition = "kept" if payload.action == "keep" else "archived"
        else:
            if entry.expected_revision is None or entry.client_action_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="Work item revision and action ID are required",
                )
            bind_client_action(session, entry.client_action_id)
            try:
                await archive_work_item(
                    session,
                    identity.user.id,
                    entry.id,
                    None,
                    expected_revision=entry.expected_revision,
                )
            except (ValueError, InvalidWorkItemTransitionError) as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
    await session.flush()
    return {"processed": len(payload.entries)}
