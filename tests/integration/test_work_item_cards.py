from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.users import create_telegram_user
from flowmate.task_engine.details import get_work_item_details
from flowmate.task_engine.management import (
    add_work_item_note,
)
from flowmate.task_engine.service import (
    append_work_item_event,
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
)


@pytest.mark.integration
async def test_work_item_details_are_owned_limited_and_enriched(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_012)
    other = await create_telegram_user(database_session, 630_013)
    topic = await create_topic(database_session, user.id, "Testing")
    person = await create_person(database_session, user.id, "Антон")
    due_at = datetime.now(UTC) + timedelta(days=1)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Detail task",
        description="Useful description",
        topic_id=topic.id,
        due_at=due_at,
    )
    await link_person_to_work_item(database_session, user.id, item.id, person.id)
    for index in range(4):
        await add_work_item_note(
            database_session,
            user.id,
            item.id,
            830_100 + index,
            f"Note {index}",
        )
    for index in range(6):
        await append_work_item_event(
            database_session,
            user.id,
            item.id,
            "updated",
            payload={"index": index},
        )

    details = await get_work_item_details(database_session, user.id, item.id)

    assert details is not None
    assert details.topic_name == "Testing"
    assert details.person_names == ("Антон",)
    assert len(details.notes) == 3
    assert len(details.events) == 5
    assert details.nearest_reminder is not None
    assert await get_work_item_details(database_session, other.id, item.id) is None
