from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import Note, NoteLink, WorkItemEvent
from flowmate.db.notes import create_note_idempotently
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.enums import (
    NoteTargetType,
    WorkItemEventType,
    WorkItemPriority,
    WorkItemRelationType,
    WorkItemStatus,
    WorkItemType,
)
from flowmate.task_engine.service import (
    append_work_item_event,
    create_linked_note,
    create_person,
    create_topic,
    create_work_item,
    create_work_item_relation,
    find_people,
    find_topics,
    get_or_create_topic,
    get_work_item,
    link_note,
    link_person_to_work_item,
    list_linked_notes,
    list_people_for_work_item,
    list_work_item_events,
    list_work_item_relations,
    list_work_items,
)


@pytest.mark.integration
async def test_all_work_item_types_statuses_and_priorities(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 610_001)

    for item_type in WorkItemType:
        item = await create_work_item(
            database_session,
            user.id,
            item_type=item_type,
            title=f"Type {item_type.value}",
        )
        assert item.type == item_type.value
        assert item.status == WorkItemStatus.INBOX.value
        assert item.priority == WorkItemPriority.NORMAL.value

    for status in WorkItemStatus:
        item = await create_work_item(
            database_session,
            user.id,
            item_type=WorkItemType.TASK,
            title=f"Status {status.value}",
            status=status,
        )
        assert item.status == status.value

    for priority in WorkItemPriority:
        item = await create_work_item(
            database_session,
            user.id,
            item_type=WorkItemType.TASK,
            title=f"Priority {priority.value}",
            priority=priority,
        )
        assert item.priority == priority.value

    items = await list_work_items(database_session, user.id)
    assert len(items) == len(WorkItemType) + len(WorkItemStatus) + len(WorkItemPriority)
    assert await database_session.scalar(select(func.count(WorkItemEvent.id))) == len(
        items
    )


@pytest.mark.integration
async def test_topics_people_aliases_and_duplicate_topic_name(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 610_002)
    topic, created = await get_or_create_topic(
        database_session,
        user.id,
        " Client   Alpha ",
        aliases=["Project X", "CLIENT ALPHA"],
    )
    duplicate, duplicate_created = await get_or_create_topic(
        database_session,
        user.id,
        " client alpha ",
        description="Must not overwrite",
    )
    first_person = await create_person(
        database_session,
        user.id,
        "Alex",
        role="Lead",
        aliases=["Sasha"],
    )
    second_person = await create_person(database_session, user.id, "Alex")

    assert created is True
    assert duplicate_created is False
    assert duplicate.id == topic.id
    assert duplicate.description is None
    assert topic.aliases == ["project x"]
    assert [
        match.id for match in await find_topics(database_session, user.id, "PROJECT X")
    ] == [topic.id]
    assert {
        person.id for person in await find_people(database_session, user.id, "Alex")
    } == {
        first_person.id,
        second_person.id,
    }
    assert [
        person.id for person in await find_people(database_session, user.id, "sasha")
    ] == [first_person.id]

    with pytest.raises(IntegrityError):
        async with database_session.begin_nested():
            await create_topic(database_session, user.id, "CLIENT ALPHA")


@pytest.mark.integration
async def test_work_item_references_and_retrieval_are_user_isolated(
    database_session: AsyncSession,
) -> None:
    owner = await create_telegram_user(database_session, 610_003)
    other = await create_telegram_user(database_session, 610_004)
    topic = await create_topic(database_session, owner.id, "Private topic")
    source_note, _ = await create_note_idempotently(
        database_session,
        user_id=owner.id,
        content="Private source",
        source="text",
        telegram_update_id=710_001,
    )
    item = await create_work_item(
        database_session,
        owner.id,
        item_type="follow_up",
        title="Contact client",
        topic_id=topic.id,
        source_note_id=source_note.id,
        due_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )

    assert await get_work_item(database_session, owner.id, item.id) is item
    assert await get_work_item(database_session, other.id, item.id) is None
    assert await list_work_items(database_session, other.id) == []
    with pytest.raises(ValueError, match="topic not found"):
        await create_work_item(
            database_session,
            other.id,
            item_type="task",
            title="Cross-user topic",
            topic_id=topic.id,
        )
    with pytest.raises(ValueError, match="note not found"):
        await create_work_item(
            database_session,
            other.id,
            item_type="task",
            title="Cross-user note",
            source_note_id=source_note.id,
        )


@pytest.mark.integration
async def test_people_relations_and_history(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 610_005)
    other = await create_telegram_user(database_session, 610_006)
    source = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Prepare proposal",
    )
    target = await create_work_item(
        database_session,
        user.id,
        item_type="waiting",
        title="Receive approval",
    )
    person = await create_person(database_session, user.id, "Maria")
    association, linked = await link_person_to_work_item(
        database_session,
        user.id,
        source.id,
        person.id,
        role="owner",
    )
    duplicate, duplicate_linked = await link_person_to_work_item(
        database_session,
        user.id,
        source.id,
        person.id,
    )
    relation = await create_work_item_relation(
        database_session,
        user.id,
        source.id,
        target.id,
        WorkItemRelationType.BLOCKED_BY,
    )
    event = await append_work_item_event(
        database_session,
        user.id,
        source.id,
        WorkItemEventType.STATUS_CHANGED,
        payload={"from": "inbox", "to": "active"},
    )

    assert linked is True
    assert duplicate_linked is False
    assert duplicate.id == association.id
    assert [
        value.id
        for value in await list_people_for_work_item(
            database_session, user.id, source.id
        )
    ] == [person.id]
    assert [
        value.id
        for value in await list_work_item_relations(
            database_session, user.id, source.id
        )
    ] == [relation.id]
    events = await list_work_item_events(database_session, user.id, source.id)
    assert [value.event_type for value in events] == ["created", "status_changed"]
    assert events[-1].id == event.id
    assert await list_people_for_work_item(database_session, other.id, source.id) == []
    assert await list_work_item_relations(database_session, other.id, source.id) == []
    assert await list_work_item_events(database_session, other.id, source.id) == []

    with pytest.raises(ValueError, match="cannot reference itself"):
        await create_work_item_relation(
            database_session,
            user.id,
            source.id,
            source.id,
            "related_to",
        )


@pytest.mark.integration
async def test_manual_and_existing_notes_link_to_each_target_once(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 610_007)
    other = await create_telegram_user(database_session, 610_008)
    topic = await create_topic(database_session, user.id, "Release")
    person = await create_person(database_session, user.id, "Anton")
    item = await create_work_item(
        database_session,
        user.id,
        item_type="agenda_item",
        title="Release review",
    )
    work_note, work_link = await create_linked_note(
        database_session,
        user.id,
        content="  Prepare   metrics  ",
        target_type=NoteTargetType.WORK_ITEM,
        target_id=item.id,
    )
    person_note, _ = await create_linked_note(
        database_session,
        user.id,
        content="Prefers morning calls",
        target_type=NoteTargetType.PERSON,
        target_id=person.id,
    )
    topic_note, _ = await create_linked_note(
        database_session,
        user.id,
        content="Release scope",
        target_type=NoteTargetType.TOPIC,
        target_id=topic.id,
    )
    telegram_note, _ = await create_note_idempotently(
        database_session,
        user_id=user.id,
        content="Telegram context",
        source="text",
        telegram_update_id=710_002,
    )
    first_link, created = await link_note(
        database_session,
        user.id,
        telegram_note.id,
        NoteTargetType.WORK_ITEM,
        item.id,
    )
    duplicate_link, duplicate_created = await link_note(
        database_session,
        user.id,
        telegram_note.id,
        NoteTargetType.WORK_ITEM,
        item.id,
    )

    assert work_note.content == "Prepare metrics"
    assert work_note.source == "manual"
    assert work_note.telegram_update_id is None
    assert work_link.work_item_id == item.id
    assert person_note.source == "manual"
    assert topic_note.source == "manual"
    assert created is True
    assert duplicate_created is False
    assert duplicate_link.id == first_link.id
    assert {
        note.id
        for note in await list_linked_notes(
            database_session, user.id, "work_item", item.id
        )
    } == {telegram_note.id, work_note.id}
    assert [
        note.id
        for note in await list_linked_notes(
            database_session, user.id, "person", person.id
        )
    ] == [person_note.id]
    assert [
        note.id
        for note in await list_linked_notes(
            database_session, user.id, "topic", topic.id
        )
    ] == [topic_note.id]
    assert (
        await list_linked_notes(database_session, other.id, "work_item", item.id) == []
    )

    invalid_note = Note(
        user_id=user.id,
        content="Invalid link",
        source="manual",
        telegram_update_id=None,
    )
    database_session.add(invalid_note)
    await database_session.flush()
    with pytest.raises(IntegrityError):
        async with database_session.begin_nested():
            database_session.add(
                NoteLink(
                    user_id=user.id,
                    note_id=invalid_note.id,
                    work_item_id=item.id,
                    person_id=person.id,
                )
            )
            await database_session.flush()

    assert await database_session.scalar(select(func.count(NoteLink.id))) == 4
    assert await database_session.scalar(select(func.count(Note.id))) == 5
