from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import (
    DependencyCandidate,
    DependencyRelation,
    DraftItem,
    DraftItemType,
)
from flowmate.db.drafts import create_parsing_draft, replace_draft_analysis
from flowmate.db.models import (
    DraftSession,
    Note,
    NoteLink,
    Person,
    Topic,
    User,
    WorkItem,
    WorkItemPerson,
    WorkItemRelation,
)
from flowmate.db.notes import create_note_idempotently
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.conversion import (
    DraftConversionIntegrityError,
    DraftConversionService,
    DraftNotConvertibleError,
)
from flowmate.task_engine.service import create_person, create_topic
from tests.ai_factories import (
    make_analysis_result,
    make_draft_item,
    make_parse_result,
    make_temporal_candidate,
)


async def create_draft(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    update_id: int,
    items: list[DraftItem],
) -> tuple[User, Note, DraftSession]:
    user = await create_telegram_user(session, telegram_user_id)
    note, _ = await create_note_idempotently(
        session,
        user_id=user.id,
        content="Original transcription",
        source="voice",
        telegram_update_id=update_id,
    )
    draft = await create_parsing_draft(
        session,
        user_id=user.id,
        source_note_id=note.id,
        ttl_hours=24,
    )
    analysis = make_analysis_result(make_parse_result(items))
    await replace_draft_analysis(
        session,
        draft,
        analysis,
        question=None,
        ttl_hours=24,
    )
    return user, note, draft


@pytest.mark.integration
async def test_conversion_creates_all_types_with_provenance_and_is_idempotent(
    database_session: AsyncSession,
) -> None:
    item_types = [
        DraftItemType.TASK,
        DraftItemType.FOLLOW_UP,
        DraftItemType.WAITING,
        DraftItemType.QUESTION,
        DraftItemType.DECISION,
        DraftItemType.AGENDA_ITEM,
        DraftItemType.NOTE,
    ]
    items = [
        make_draft_item(type=item_type, title=f"Item {item_type.value}")
        for item_type in item_types
    ]
    user, source_note, draft = await create_draft(
        database_session,
        telegram_user_id=620_001,
        update_id=720_001,
        items=items,
    )
    service = DraftConversionService(
        clock=lambda: datetime(2026, 7, 21, 10, tzinfo=UTC)
    )

    first = await service.convert(
        database_session,
        draft_id=draft.id,
        user_id=user.id,
    )
    await database_session.flush()
    second = await service.convert(
        database_session,
        draft_id=draft.id,
        user_id=user.id,
    )

    assert len(first.work_items) == 6
    assert len(first.notes) == 1
    assert {item.type for item in first.work_items} == {
        item_type.value
        for item_type in item_types
        if item_type is not DraftItemType.NOTE
    }
    waiting = next(item for item in first.work_items if item.type == "waiting")
    assert waiting.status == "waiting"
    assert waiting.waiting_since == datetime(2026, 7, 21, 10, tzinfo=UTC)
    assert all(item.source_note_id == source_note.id for item in first.work_items)
    assert all(item.source_draft_item_id is not None for item in first.work_items)
    assert first.notes[0].source_draft_item_id is not None
    assert first.notes[0].content == "Item note"
    assert source_note.content == "Original transcription"
    assert draft.status == "confirmed"
    assert {item.id for item in second.work_items} == {
        item.id for item in first.work_items
    }
    assert {note.id for note in second.notes} == {note.id for note in first.notes}
    assert await database_session.scalar(select(func.count(WorkItem.id))) == 6
    assert await database_session.scalar(select(func.count(Note.id))) == 2


@pytest.mark.integration
async def test_conversion_resolves_candidates_dates_and_dependencies(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 620_002)
    topic = await create_topic(
        database_session,
        user.id,
        "Client Alpha",
        aliases=["Project X"],
    )
    person = await create_person(
        database_session,
        user.id,
        "Maria",
        aliases=["Masha"],
    )
    source_note, _ = await create_note_idempotently(
        database_session,
        user_id=user.id,
        content="Source",
        source="text",
        telegram_update_id=720_002,
    )
    draft = await create_parsing_draft(
        database_session,
        user_id=user.id,
        source_note_id=source_note.id,
        ttl_hours=24,
    )
    due = make_temporal_candidate(
        normalized_value=datetime(2026, 7, 22, 14, tzinfo=UTC)
    )
    reminder = make_temporal_candidate(
        original_phrase="remind at 13:00",
        normalized_value=datetime(2026, 7, 22, 13, tzinfo=UTC),
    )
    items = [
        make_draft_item(
            type=DraftItemType.TASK,
            title="Ask Maria",
            person_candidates=["Masha", "команда"],
            topic_candidates=["Project X"],
            due_date_candidate=due,
        ),
        make_draft_item(
            type=DraftItemType.FOLLOW_UP,
            title="Contact Anton",
            person_candidates=["Anton"],
            topic_candidates=["New topic"],
            due_date_candidate=due,
            reminder_candidate=reminder,
            dependencies=[
                DependencyCandidate(
                    relation=DependencyRelation.AFTER,
                    original_phrase="после ответа",
                    target_item_number=1,
                    condition=None,
                ),
                DependencyCandidate(
                    relation=DependencyRelation.BLOCKED_BY,
                    original_phrase="blocked by answer",
                    target_item_number=1,
                    condition=None,
                ),
            ],
        ),
        make_draft_item(
            type=DraftItemType.DECISION,
            title="Record decision",
            dependencies=[
                DependencyCandidate(
                    relation=DependencyRelation.BEFORE,
                    original_phrase="сначала решение",
                    target_item_number=2,
                    condition=None,
                ),
                DependencyCandidate(
                    relation=DependencyRelation.WAITING_FOR,
                    original_phrase="ждёт ответа",
                    target_item_number=1,
                    condition=None,
                ),
            ],
        ),
        make_draft_item(
            type=DraftItemType.NOTE,
            title="Client context",
            person_candidates=["Maria"],
            topic_candidates=["Client Alpha"],
        ),
    ]
    analysis = make_analysis_result(make_parse_result(items))
    await replace_draft_analysis(
        database_session,
        draft,
        analysis,
        question=None,
        ttl_hours=24,
    )

    result = await DraftConversionService().convert(
        database_session,
        draft_id=draft.id,
        user_id=user.id,
    )
    first, second, third = result.work_items

    assert first.topic_id == topic.id
    assert first.due_at == due.normalized_value
    assert second.due_at == due.normalized_value
    assert second.next_follow_up_at == reminder.normalized_value
    assert third.type == "decision"
    assert await database_session.scalar(select(func.count(Topic.id))) == 2
    assert await database_session.scalar(select(func.count(Person.id))) == 2
    associations = list(await database_session.scalars(select(WorkItemPerson)))
    assert {association.person_id for association in associations} == {
        person.id,
        next(
            value.id
            for value in await database_session.scalars(select(Person))
            if value.display_name == "Anton"
        ),
    }
    relations = list(await database_session.scalars(select(WorkItemRelation)))
    assert {relation.relation_type for relation in relations} == {
        "after_completion",
        "blocked_by",
        "waiting_for",
    }
    links = list(await database_session.scalars(select(NoteLink)))
    assert {link.person_id for link in links if link.person_id is not None} == {
        person.id
    }
    assert {link.topic_id for link in links if link.topic_id is not None} == {topic.id}


@pytest.mark.integration
async def test_conversion_rejects_unknown_user_and_partial_outputs(
    database_session: AsyncSession,
) -> None:
    user, _, draft = await create_draft(
        database_session,
        telegram_user_id=620_003,
        update_id=720_003,
        items=[make_draft_item(), make_draft_item(title="Second")],
    )
    other = await create_telegram_user(database_session, 620_004)
    service = DraftConversionService()

    with pytest.raises(DraftNotConvertibleError, match="not found"):
        await service.convert(
            database_session,
            draft_id=draft.id,
            user_id=other.id,
        )

    record = draft.items[0]
    database_session.add(
        WorkItem(
            user_id=user.id,
            type="task",
            title="Partial",
            status="inbox",
            priority="normal",
            source_draft_item_id=record.id,
        )
    )
    await database_session.flush()
    with pytest.raises(DraftConversionIntegrityError, match="partially stored"):
        await service.convert(
            database_session,
            draft_id=draft.id,
            user_id=user.id,
        )


@pytest.mark.integration
async def test_conversion_skips_ambiguous_people_and_topics(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 620_007)
    await create_person(database_session, user.id, "Alex")
    await create_person(database_session, user.id, "Alex")
    await create_topic(database_session, user.id, "Alpha", aliases=["Project"])
    await create_topic(database_session, user.id, "Beta", aliases=["Project"])
    source_note, _ = await create_note_idempotently(
        database_session,
        user_id=user.id,
        content="Ambiguous source",
        source="text",
        telegram_update_id=720_007,
    )
    draft = await create_parsing_draft(
        database_session,
        user_id=user.id,
        source_note_id=source_note.id,
        ttl_hours=24,
    )
    analysis = make_analysis_result(
        make_parse_result(
            [
                make_draft_item(
                    person_candidates=["Alex"],
                    topic_candidates=["Project"],
                )
            ]
        )
    )
    await replace_draft_analysis(
        database_session,
        draft,
        analysis,
        question=None,
        ttl_hours=24,
    )

    result = await DraftConversionService().convert(
        database_session,
        draft_id=draft.id,
        user_id=user.id,
    )

    assert result.work_items[0].topic_id is None
    assert (
        await database_session.scalar(
            select(func.count(WorkItemPerson.id)).where(
                WorkItemPerson.work_item_id == result.work_items[0].id
            )
        )
        == 0
    )
    assert await database_session.scalar(select(func.count(Person.id))) == 2
    assert await database_session.scalar(select(func.count(Topic.id))) == 2


@pytest.mark.integration
async def test_invalid_item_creates_no_partial_records(
    database_session: AsyncSession,
) -> None:
    user, _, draft = await create_draft(
        database_session,
        telegram_user_id=620_005,
        update_id=720_005,
        items=[
            make_draft_item(title="Would otherwise be created"),
            make_draft_item(type=DraftItemType.UNKNOWN, title="Unknown"),
        ],
    )

    with pytest.raises(DraftNotConvertibleError, match="unknown"):
        await DraftConversionService().convert(
            database_session,
            draft_id=draft.id,
            user_id=user.id,
            allow_incomplete=True,
        )
    await database_session.rollback()

    assert (
        await database_session.scalar(
            select(func.count(WorkItem.id)).where(WorkItem.user_id == user.id)
        )
        == 0
    )
    assert (
        await database_session.scalar(
            select(func.count(Note.id)).where(
                Note.user_id == user.id,
                Note.source == "manual",
            )
        )
        == 0
    )


@pytest.mark.integration
async def test_caller_rollback_removes_every_conversion_output(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, draft = await create_draft(
        database_session,
        telegram_user_id=620_006,
        update_id=720_006,
        items=[
            make_draft_item(title="First"),
            make_draft_item(type=DraftItemType.NOTE, title="Second"),
        ],
    )
    await database_session.commit()
    user_id = user.id
    draft_id = draft.id
    service = DraftConversionService()
    monkeypatch.setattr(
        service,
        "_create_relations",
        AsyncMock(side_effect=SQLAlchemyError("injected relation failure")),
    )

    with pytest.raises(SQLAlchemyError, match="injected relation failure"):
        await service.convert(
            database_session,
            draft_id=draft_id,
            user_id=user_id,
        )
    await database_session.rollback()

    assert (
        await database_session.scalar(
            select(func.count(WorkItem.id)).where(WorkItem.user_id == user_id)
        )
        == 0
    )
    assert (
        await database_session.scalar(
            select(func.count(Note.id)).where(
                Note.user_id == user_id,
                Note.source == "manual",
            )
        )
        == 0
    )
    reloaded = await database_session.get(DraftSession, draft_id)
    assert reloaded is not None
    assert reloaded.status == "ready"
