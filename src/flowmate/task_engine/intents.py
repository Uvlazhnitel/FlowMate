from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import ManagementAction, ManagementIntent
from flowmate.db.models import (
    Person,
    Topic,
    User,
    WorkItem,
    WorkItemActionSession,
    WorkItemEvent,
)
from flowmate.task_engine.enums import WorkItemType
from flowmate.task_engine.queries import find_matching_work_items
from flowmate.task_engine.service import (
    create_person,
    find_people,
    find_topics,
    get_or_create_topic,
    get_work_item,
    list_people_for_work_item,
)


class AmbiguousManagementCandidateError(ValueError):
    pass


async def management_update_was_processed(
    session: AsyncSession,
    telegram_user_id: int,
    telegram_update_id: int,
) -> bool:
    if telegram_update_id <= 0:
        raise ValueError("telegram_update_id must be positive")
    event_id = await session.scalar(
        select(WorkItemEvent.id)
        .join(User, User.id == WorkItemEvent.user_id)
        .where(
            User.telegram_user_id == telegram_user_id,
            WorkItemEvent.telegram_update_id == telegram_update_id,
        )
    )
    if event_id is not None:
        return True
    action_session_id = await session.scalar(
        select(WorkItemActionSession.id)
        .join(User, User.id == WorkItemActionSession.user_id)
        .where(
            User.telegram_user_id == telegram_user_id,
            or_(
                WorkItemActionSession.telegram_update_id == telegram_update_id,
                WorkItemActionSession.context.contains(
                    {"processed_update_ids": [telegram_update_id]}
                ),
            ),
        )
    )
    return action_session_id is not None


async def find_intent_targets(
    session: AsyncSession,
    user_id: UUID,
    intent: ManagementIntent,
    *,
    contextual_work_item_id: UUID | None = None,
    limit: int = 11,
) -> list[WorkItem]:
    if contextual_work_item_id is not None:
        contextual = await get_work_item(session, user_id, contextual_work_item_id)
        return [contextual] if contextual is not None else []
    item_type = (
        intent.target_type.value
        if intent.target_type is not None
        and intent.target_type.value in {value.value for value in WorkItemType}
        else None
    )
    return await find_matching_work_items(
        session,
        user_id,
        query=intent.record_query,
        item_type=item_type,
        person_query=intent.person_candidate,
        topic_query=(
            intent.topic_candidate
            if intent.action is not ManagementAction.CHANGE_TOPIC
            else None
        ),
        include_completed=intent.action is ManagementAction.REOPEN,
        limit=limit,
    )


async def resolve_topic_candidate(
    session: AsyncSession,
    user_id: UUID,
    candidate: str,
) -> Topic:
    matches = await find_topics(session, user_id, candidate)
    if len(matches) > 1:
        raise AmbiguousManagementCandidateError("multiple topics match")
    if matches:
        return matches[0]
    topic, _ = await get_or_create_topic(session, user_id, candidate)
    return topic


async def resolve_person_candidate(
    session: AsyncSession,
    user_id: UUID,
    candidate: str,
) -> Person:
    matches = await find_people(session, user_id, candidate)
    if len(matches) > 1:
        raise AmbiguousManagementCandidateError("multiple people match")
    if matches:
        return matches[0]
    return await create_person(session, user_id, candidate)


async def resolve_replaced_person_id(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
) -> UUID:
    linked_people = await list_people_for_work_item(session, user_id, work_item_id)
    if len(linked_people) != 1:
        raise AmbiguousManagementCandidateError(
            "replacement requires exactly one linked person"
        )
    return linked_people[0].id
