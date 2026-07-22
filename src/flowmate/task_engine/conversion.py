import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowmate.ai.schemas import (
    DependencyRelation,
    DraftItemAssessment,
    DraftItemType,
    TemporalStatus,
)
from flowmate.db.drafts import transition_draft
from flowmate.db.models import (
    DraftItemPerson,
    DraftItemRecord,
    DraftSession,
    Note,
    Person,
    Topic,
    WorkItem,
)
from flowmate.reminders.sync import ReminderPolicy
from flowmate.stabilization.audit import record_audit_event
from flowmate.task_engine.enums import (
    NoteTargetType,
    PlannerStatus,
    WorkItemRelationType,
    WorkItemStatus,
    WorkItemType,
)
from flowmate.task_engine.service import (
    create_person,
    create_work_item,
    create_work_item_relation,
    find_people,
    find_topics,
    get_or_create_topic,
    link_note,
    link_person_to_work_item,
    normalize_required_text,
)

ACTIONABLE_TYPES = {
    DraftItemType.TASK: WorkItemType.TASK,
    DraftItemType.FOLLOW_UP: WorkItemType.FOLLOW_UP,
    DraftItemType.WAITING: WorkItemType.WAITING,
    DraftItemType.QUESTION: WorkItemType.QUESTION,
    DraftItemType.DECISION: WorkItemType.DECISION,
    DraftItemType.AGENDA_ITEM: WorkItemType.AGENDA_ITEM,
}
VAGUE_PEOPLE = {
    "кто-то",
    "кто нибудь",
    "кто-нибудь",
    "команда",
    "коллеги",
    "someone",
    "somebody",
    "team",
    "people",
}


class DraftConversionError(Exception):
    """Base error for safe draft conversion failures."""


class DraftNotConvertibleError(DraftConversionError):
    """The draft cannot be converted in its current state."""


class DraftConversionIntegrityError(DraftConversionError):
    """Persisted conversion provenance is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class DraftConversionResult:
    draft_id: UUID
    work_items: tuple[WorkItem, ...]
    notes: tuple[Note, ...]
    counts: dict[DraftItemType, int]


def conversion_summary(result: DraftConversionResult) -> str:
    labels = (
        (DraftItemType.TASK, "задачи"),
        (DraftItemType.FOLLOW_UP, "follow-up"),
        (DraftItemType.WAITING, "ожидания"),
        (DraftItemType.QUESTION, "вопросы"),
        (DraftItemType.DECISION, "решения"),
        (DraftItemType.AGENDA_ITEM, "пункты повестки"),
        (DraftItemType.NOTE, "заметки"),
    )
    values = "; ".join(
        f"{label} — {result.counts.get(item_type, 0)}" for item_type, label in labels
    )
    return f"Черновик подтверждён. Создано: {values}."


def normalize_candidate(value: str) -> str:
    return " ".join(value.split()).casefold()


def combine_description(description: str | None, notes: list[str]) -> str | None:
    parts = [part.strip() for part in [description, *notes] if part and part.strip()]
    return "\n\n".join(parts) or None


def note_content(assessment: DraftItemAssessment) -> str:
    item = assessment.item
    parts = [item.title, item.description, *item.notes]
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


class DraftConversionService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        reminder_policy: ReminderPolicy | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._reminder_policy = reminder_policy

    async def convert(
        self,
        session: AsyncSession,
        *,
        draft_id: UUID,
        user_id: UUID,
        allow_incomplete: bool = False,
        selected_item_ids: set[UUID] | None = None,
        status_overrides: dict[UUID, WorkItemStatus] | None = None,
        planner_overrides: dict[UUID, PlannerStatus] | None = None,
    ) -> DraftConversionResult:
        draft = await self._lock_draft(session, draft_id, user_id)
        if draft is None:
            raise DraftNotConvertibleError("draft not found")
        if draft.status not in {"ready", "confirmed"} and not (
            allow_incomplete and draft.status == "needs_clarification"
        ):
            raise DraftNotConvertibleError("draft status cannot be converted")
        records_to_convert = [
            item
            for item in draft.items
            if selected_item_ids is None or item.id in selected_item_ids
        ]
        if not records_to_convert:
            raise DraftNotConvertibleError("draft has no items")

        if (
            selected_item_ids is not None
            and {item.id for item in records_to_convert} != selected_item_ids
        ):
            raise DraftNotConvertibleError("selected draft item not found")
        existing = await self._existing_outputs(session, draft, records_to_convert)
        if existing is not None:
            if selected_item_ids is None and draft.status != "confirmed":
                await transition_draft(session, draft, "confirmed")
            return existing

        assessments = self._validate_items(records_to_convert)
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise DraftConversionError("conversion clock must be timezone-aware")

        people_cache: dict[str, Person | None] = {}
        records: dict[int, WorkItem | Note] = {}
        work_items: list[WorkItem] = []
        notes: list[Note] = []
        counts: Counter[DraftItemType] = Counter()

        for record, assessment in zip(records_to_convert, assessments, strict=True):
            item = assessment.item
            topic = await self._resolve_topic(
                session,
                user_id,
                record,
                item.topic_candidates,
            )
            people = await self._resolve_people(
                session,
                user_id,
                record,
                item.person_candidates,
                people_cache,
            )
            if item.type is DraftItemType.NOTE:
                note = Note(
                    user_id=user_id,
                    content=normalize_required_text(
                        note_content(assessment), "content"
                    ),
                    source="manual",
                    telegram_update_id=None,
                    source_draft_item_id=record.id,
                )
                session.add(note)
                await session.flush()
                for person in people:
                    await link_note(
                        session,
                        user_id,
                        note.id,
                        NoteTargetType.PERSON,
                        person.id,
                    )
                if topic is not None:
                    await link_note(
                        session,
                        user_id,
                        note.id,
                        NoteTargetType.TOPIC,
                        topic.id,
                    )
                records[record.position] = note
                notes.append(note)
                counts[item.type] += 1
                continue

            work_item = await self._create_work_item(
                session,
                draft,
                record,
                assessment,
                topic,
                now,
                status_override=(status_overrides or {}).get(record.id),
            )
            work_item.planner_status = (
                (planner_overrides or {}).get(record.id)
                or PlannerStatus(work_item.planner_status)
            ).value
            for person in people:
                await link_person_to_work_item(
                    session,
                    user_id,
                    work_item.id,
                    person.id,
                )
            records[record.position] = work_item
            work_items.append(work_item)
            counts[item.type] += 1

        await self._create_relations(
            session, user_id, records_to_convert, assessments, records
        )
        if selected_item_ids is None:
            await transition_draft(session, draft, "confirmed")
        await record_audit_event(
            session,
            actor_kind="system",
            action="draft.converted",
            outcome="success",
            user_id=user_id,
            entity_kind="draft",
            entity_id=draft.id,
            safe_metadata={"count": len(records)},
        )
        return DraftConversionResult(
            draft_id=draft.id,
            work_items=tuple(work_items),
            notes=tuple(notes),
            counts=dict(counts),
        )

    async def _lock_draft(
        self,
        session: AsyncSession,
        draft_id: UUID,
        user_id: UUID,
    ) -> DraftSession | None:
        statement = (
            select(DraftSession)
            .options(selectinload(DraftSession.items))
            .where(DraftSession.id == draft_id, DraftSession.user_id == user_id)
            .with_for_update()
        )
        return (await session.scalars(statement)).one_or_none()

    async def _existing_outputs(
        self,
        session: AsyncSession,
        draft: DraftSession,
        records: list[DraftItemRecord],
    ) -> DraftConversionResult | None:
        source_ids = [item.id for item in records]
        work_items = list(
            await session.scalars(
                select(WorkItem).where(
                    WorkItem.user_id == draft.user_id,
                    WorkItem.source_draft_item_id.in_(source_ids),
                )
            )
        )
        notes = list(
            await session.scalars(
                select(Note).where(
                    Note.user_id == draft.user_id,
                    Note.source_draft_item_id.in_(source_ids),
                )
            )
        )
        output_ids = [value.source_draft_item_id for value in work_items]
        output_ids.extend(value.source_draft_item_id for value in notes)
        if not output_ids:
            return None
        if len(output_ids) != len(source_ids) or set(output_ids) != set(source_ids):
            raise DraftConversionIntegrityError("draft conversion is partially stored")
        work_by_source = {item.source_draft_item_id: item for item in work_items}
        notes_by_source = {note.source_draft_item_id: note for note in notes}
        for draft_item in records:
            item_type = DraftItemType(draft_item.item_type)
            if item_type is DraftItemType.NOTE:
                consistent = draft_item.id in notes_by_source
            else:
                work_item = work_by_source.get(draft_item.id)
                consistent = (
                    work_item is not None
                    and work_item.type == ACTIONABLE_TYPES[item_type].value
                )
            if not consistent:
                raise DraftConversionIntegrityError(
                    "draft conversion provenance is inconsistent"
                )
        counts = Counter(DraftItemType(item.item_type) for item in records)
        return DraftConversionResult(
            draft_id=draft.id,
            work_items=tuple(work_items),
            notes=tuple(notes),
            counts=dict(counts),
        )

    def _validate_items(
        self,
        records: list[DraftItemRecord],
    ) -> list[DraftItemAssessment]:
        assessments: list[DraftItemAssessment] = []
        for record in records:
            try:
                assessment = DraftItemAssessment.model_validate_json(
                    json.dumps(record.raw_payload)
                )
            except ValueError as error:
                raise DraftNotConvertibleError("invalid draft item payload") from error
            if assessment.item.type is DraftItemType.UNKNOWN:
                raise DraftNotConvertibleError("unknown draft item cannot be converted")
            assessments.append(assessment)
        return assessments

    async def _resolve_topic(
        self,
        session: AsyncSession,
        user_id: UUID,
        record: DraftItemRecord,
        candidates: list[str],
    ) -> Topic | None:
        if record.selected_topic_id is not None:
            topic = await session.get(Topic, record.selected_topic_id)
            if topic is None or topic.user_id != user_id:
                raise DraftConversionIntegrityError("selected topic is not owned")
            return topic
        unique = {normalize_candidate(value): value for value in candidates}
        if len(unique) != 1:
            return None
        candidate = next(iter(unique.values()))
        matches = await find_topics(session, user_id, candidate)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
        topic, _ = await get_or_create_topic(session, user_id, candidate)
        return topic

    async def _resolve_people(
        self,
        session: AsyncSession,
        user_id: UUID,
        record: DraftItemRecord,
        candidates: list[str],
        cache: dict[str, Person | None],
    ) -> list[Person]:
        selected = list(
            await session.scalars(
                select(Person)
                .join(DraftItemPerson, DraftItemPerson.person_id == Person.id)
                .where(
                    DraftItemPerson.user_id == user_id,
                    DraftItemPerson.draft_item_id == record.id,
                    Person.user_id == user_id,
                )
                .order_by(Person.display_name, Person.id)
            )
        )
        if selected:
            return selected
        resolved: list[Person] = []
        seen: set[UUID] = set()
        for candidate in candidates:
            normalized = normalize_candidate(candidate)
            if not normalized or normalized in VAGUE_PEOPLE:
                continue
            if normalized not in cache:
                matches = await find_people(session, user_id, candidate)
                if len(matches) == 1:
                    cache[normalized] = matches[0]
                elif len(matches) > 1:
                    cache[normalized] = None
                else:
                    cache[normalized] = await create_person(
                        session,
                        user_id,
                        candidate,
                    )
            person = cache[normalized]
            if person is not None and person.id not in seen:
                resolved.append(person)
                seen.add(person.id)
        return resolved

    async def _create_work_item(
        self,
        session: AsyncSession,
        draft: DraftSession,
        record: DraftItemRecord,
        assessment: DraftItemAssessment,
        topic: Topic | None,
        now: datetime,
        status_override: WorkItemStatus | None = None,
    ) -> WorkItem:
        item = assessment.item
        item_type = ACTIONABLE_TYPES[item.type]
        due = (
            item.due_date_candidate.normalized_value
            if item.due_date_candidate is not None
            and item.due_date_candidate.status is TemporalStatus.RESOLVED
            else None
        )
        reminder = (
            item.reminder_candidate.normalized_value
            if item.reminder_candidate is not None
            and item.reminder_candidate.status is TemporalStatus.RESOLVED
            else None
        )
        next_follow_up = due if item.type is DraftItemType.FOLLOW_UP else None
        if reminder is not None:
            next_follow_up = reminder
        status = status_override or (
            WorkItemStatus.WAITING
            if item.type is DraftItemType.WAITING
            else WorkItemStatus.INBOX
        )
        return await create_work_item(
            session,
            draft.user_id,
            item_type=item_type,
            title=item.title,
            description=combine_description(item.description, item.notes),
            priority=record.selected_priority,
            status=status,
            topic_id=topic.id if topic is not None else None,
            due_at=due,
            next_follow_up_at=next_follow_up,
            waiting_since=now if status is WorkItemStatus.WAITING else None,
            completed_at=now if status is WorkItemStatus.DONE else None,
            source_note_id=draft.source_note_id,
            source_draft_item_id=record.id,
            reminder_policy=self._reminder_policy,
            reminder_now=now,
        )

    async def _create_relations(
        self,
        session: AsyncSession,
        user_id: UUID,
        source_records: list[DraftItemRecord],
        assessments: list[DraftItemAssessment],
        records: dict[int, WorkItem | Note],
    ) -> None:
        created: set[tuple[UUID, UUID, WorkItemRelationType]] = set()
        for source_record, assessment in zip(source_records, assessments, strict=True):
            current = records[source_record.position]
            if not isinstance(current, WorkItem):
                continue
            for dependency in assessment.item.dependencies:
                target_number = dependency.target_item_number
                if target_number is None:
                    continue
                target = records.get(target_number)
                if not isinstance(target, WorkItem):
                    continue
                if dependency.relation is DependencyRelation.BEFORE:
                    source, destination = target, current
                    relation_type = WorkItemRelationType.AFTER_COMPLETION
                else:
                    source, destination = current, target
                    relation_type_candidate = {
                        DependencyRelation.AFTER: WorkItemRelationType.AFTER_COMPLETION,
                        DependencyRelation.BLOCKED_BY: WorkItemRelationType.BLOCKED_BY,
                        DependencyRelation.WAITING_FOR: (
                            WorkItemRelationType.WAITING_FOR
                        ),
                    }.get(dependency.relation)
                    if relation_type_candidate is None:
                        continue
                    relation_type = relation_type_candidate
                key = (source.id, destination.id, relation_type)
                if key in created:
                    continue
                await create_work_item_relation(
                    session,
                    user_id,
                    source.id,
                    destination.id,
                    relation_type,
                )
                created.add(key)
