from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.handlers.navigation.lists import build_navigation_page
from flowmate.bot.handlers.navigation.presentation import ExpiredListError
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
)


@pytest.mark.integration
async def test_work_item_pages_have_stable_boundaries_and_enrichment(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_001)
    other = await create_telegram_user(database_session, 650_002)
    topic = await create_topic(database_session, user.id, "Testing")
    person = await create_person(database_session, user.id, "Антон")
    for index in range(7):
        item = await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title=f"Task {index}",
            topic_id=topic.id if index == 0 else None,
        )
        if index == 0:
            await link_person_to_work_item(
                database_session,
                user.id,
                item.id,
                person.id,
            )
    await create_work_item(
        database_session,
        other.id,
        item_type="task",
        title="Private other-user task",
    )

    first = await build_navigation_page(
        database_session,
        user.id,
        view="t",
        page=0,
        timezone=ZoneInfo("UTC"),
    )
    last = await build_navigation_page(
        database_session,
        user.id,
        view="t",
        page=1,
        timezone=ZoneInfo("UTC"),
    )

    assert (
        len(
            [
                row
                for row in first.keyboard.inline_keyboard
                if row[0].text.endswith("Подробнее")
            ]
        )
        == 5
    )
    assert any(
        button.text == "Вперёд"
        for row in first.keyboard.inline_keyboard
        for button in row
    )
    assert "Private other-user task" not in first.text + last.text
    assert "Testing" not in first.text + last.text
    assert "Антон" not in first.text + last.text
    assert not any(
        button.text == "Вперёд"
        for row in last.keyboard.inline_keyboard
        for button in row
    )
    with pytest.raises(ExpiredListError):
        await build_navigation_page(
            database_session,
            user.id,
            view="t",
            page=2,
            timezone=ZoneInfo("UTC"),
        )
