from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftItemType,
    DraftSource,
    TemporalStatus,
)
from flowmate.ai.service import DraftParsingService
from flowmate.db.drafts import create_parsing_draft, replace_draft_analysis
from flowmate.db.models import DraftSession, Note, WorkItem
from flowmate.drafts.capture import apply_default_reminder_time, fast_capture_is_ready
from flowmate.drafts.questions import next_clarification_question
from flowmate.reminders.preferences import EffectiveNotificationPreferences
from flowmate.reminders.sync import ReminderPolicy
from flowmate.task_engine.conversion import DraftConversionService
from flowmate.task_engine.enums import WorkItemStatus
from flowmate.task_engine.management import bind_client_action, work_item_revision
from flowmate.task_engine.rescheduling import ReschedulingService
from flowmate.workspaces import workspace_context

CaptureBucket = Literal["auto", "today", "tomorrow", "inbox"]
CaptureDisposition = Literal["created", "inbox"]
CAPTURE_PROMPT_PREFIX = "pwa-text-v1:"


class CaptureConflictError(ValueError):
    """The capture key or current draft state prevents a new capture."""


@dataclass(frozen=True, slots=True)
class TextCaptureResult:
    client_capture_id: UUID
    duplicate: bool
    disposition: CaptureDisposition
    draft_id: UUID
    work_item_ids: tuple[UUID, ...]


class TextCaptureService:
    def __init__(
        self,
        parsing_service: DraftParsingService,
        rescheduling_service: ReschedulingService,
        *,
        draft_ttl_hours: int,
        high_confidence_threshold: float,
        reminder_policy: ReminderPolicy,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._parsing_service = parsing_service
        self._rescheduling_service = rescheduling_service
        self._draft_ttl_hours = draft_ttl_hours
        self._high_confidence_threshold = high_confidence_threshold
        self._reminder_policy = reminder_policy
        self._clock = clock or (lambda: datetime.now(UTC))

    async def capture(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        text: str,
        workspace: Literal["work", "personal"],
        target_bucket: CaptureBucket,
        client_capture_id: UUID,
        preferences: EffectiveNotificationPreferences,
    ) -> TextCaptureResult:
        normalized = text.strip()
        existing = await self._existing_result(
            session,
            user_id=user_id,
            text=normalized,
            workspace=workspace,
            target_bucket=target_bucket,
            client_capture_id=client_capture_id,
        )
        if existing is not None:
            return existing

        active = await session.scalar(
            select(DraftSession)
            .where(
                DraftSession.user_id == user_id,
                DraftSession.status.in_(("parsing", "needs_clarification", "ready")),
            )
            .execution_options(include_all_workspaces=True)
        )
        if active is not None:
            raise CaptureConflictError(
                "Сначала разберите текущий черновик во Входящих."
            )

        with workspace_context(session, user_id=user_id, workspace=workspace):
            note = Note(
                id=client_capture_id,
                user_id=user_id,
                content=normalized,
                source="manual",
                workspace=workspace,
            )
            session.add(note)
            await session.flush()
            draft = await create_parsing_draft(
                session,
                user_id=user_id,
                source_note_id=note.id,
                ttl_hours=self._draft_ttl_hours,
                now=self._clock(),
            )
            draft.prompt_version = f"{CAPTURE_PROMPT_PREFIX}{target_bucket}"
            analysis = await self._parsing_service.parse(
                normalized,
                source=DraftSource.TEXT,
                active_workspace=workspace,
                reference_datetime=note.created_at,
            )
            analysis = apply_default_reminder_time(
                analysis,
                default_time=preferences.default_reminder_time,
            )
            await replace_draft_analysis(
                session,
                draft,
                analysis,
                question=next_clarification_question(analysis),
                ttl_hours=self._draft_ttl_hours,
                now=self._clock(),
            )
            if not fast_capture_is_ready(
                analysis,
                high_confidence_threshold=self._high_confidence_threshold,
            ):
                return TextCaptureResult(
                    client_capture_id=client_capture_id,
                    duplicate=False,
                    disposition="inbox",
                    draft_id=draft.id,
                    work_item_ids=(),
                )

            status_overrides = self._status_overrides(draft, analysis)
            conversion = await DraftConversionService(
                reminder_policy=self._reminder_policy,
                clock=self._clock,
            ).convert(
                session,
                draft_id=draft.id,
                user_id=user_id,
                status_overrides=status_overrides,
            )
            if target_bucket != "auto":
                for item in conversion.work_items:
                    if item.type not in {"task", "follow_up", "waiting", "question"}:
                        continue
                    bind_client_action(
                        session,
                        uuid5(client_capture_id, f"bucket:{item.id}:{target_bucket}"),
                    )
                    await self._rescheduling_service.move_to_bucket(
                        session,
                        user_id,
                        item.id,
                        target_bucket,
                        preferences=preferences,
                        reminder_policy=self._reminder_policy,
                        expected_revision=work_item_revision(item.updated_at),
                        now=self._clock(),
                    )
            return TextCaptureResult(
                client_capture_id=client_capture_id,
                duplicate=False,
                disposition="created",
                draft_id=draft.id,
                work_item_ids=tuple(item.id for item in conversion.work_items),
            )

    @staticmethod
    def _status_overrides(
        draft: DraftSession,
        analysis: DraftAnalysisResult,
    ) -> dict[UUID, WorkItemStatus]:
        overrides: dict[UUID, WorkItemStatus] = {}
        for record, assessment in zip(draft.items, analysis.items, strict=True):
            item = assessment.item
            if item.type is DraftItemType.WAITING:
                overrides[record.id] = WorkItemStatus.WAITING
                continue
            if item.type not in {
                DraftItemType.TASK,
                DraftItemType.FOLLOW_UP,
                DraftItemType.QUESTION,
            }:
                continue
            due_is_resolved = (
                item.due_date_candidate is not None
                and item.due_date_candidate.status is TemporalStatus.RESOLVED
                and item.due_date_candidate.normalized_value is not None
            )
            reminder_is_resolved = (
                item.type is DraftItemType.FOLLOW_UP
                and item.reminder_candidate is not None
                and item.reminder_candidate.status is TemporalStatus.RESOLVED
                and item.reminder_candidate.normalized_value is not None
            )
            overrides[record.id] = (
                WorkItemStatus.PLANNED
                if due_is_resolved or reminder_is_resolved
                else WorkItemStatus.INBOX
            )
        return overrides

    async def _existing_result(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        text: str,
        workspace: str,
        target_bucket: CaptureBucket,
        client_capture_id: UUID,
    ) -> TextCaptureResult | None:
        note = await session.scalar(
            select(Note)
            .where(Note.id == client_capture_id)
            .execution_options(include_all_workspaces=True)
        )
        if note is None:
            return None
        if (
            note.user_id != user_id
            or note.content != text
            or note.workspace != workspace
        ):
            raise CaptureConflictError("Ключ создания уже использован другим запросом.")
        draft = await session.scalar(
            select(DraftSession)
            .options(selectinload(DraftSession.items))
            .where(
                DraftSession.source_note_id == note.id,
                DraftSession.user_id == user_id,
            )
            .execution_options(include_all_workspaces=True)
        )
        if draft is None:
            raise CaptureConflictError("Создание задачи ещё не завершено.")
        expected_prompt_version = f"{CAPTURE_PROMPT_PREFIX}{target_bucket}"
        if draft.prompt_version != expected_prompt_version:
            raise CaptureConflictError("Ключ создания уже использован другим запросом.")
        item_ids = tuple(
            await session.scalars(
                select(WorkItem.id)
                .where(
                    WorkItem.user_id == user_id,
                    WorkItem.source_draft_item_id.in_(
                        [item.id for item in draft.items]
                    ),
                )
                .execution_options(include_all_workspaces=True)
            )
        )
        return TextCaptureResult(
            client_capture_id=client_capture_id,
            duplicate=True,
            disposition="created" if draft.status == "confirmed" else "inbox",
            draft_id=draft.id,
            work_item_ids=item_ids,
        )
