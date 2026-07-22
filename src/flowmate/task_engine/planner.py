from flowmate.task_engine.enums import PlannerStatus, WorkItemStatus, WorkItemType

ELIGIBLE_PLANNER_TYPES = frozenset(
    {
        WorkItemType.TASK.value,
        WorkItemType.FOLLOW_UP.value,
        WorkItemType.WAITING.value,
    }
)
OPEN_WORK_ITEM_STATUSES = frozenset(
    {
        WorkItemStatus.INBOX.value,
        WorkItemStatus.PLANNED.value,
        WorkItemStatus.ACTIVE.value,
        WorkItemStatus.WAITING.value,
        WorkItemStatus.SNOOZED.value,
    }
)


def initial_planner_status(item_type: str, status: str) -> PlannerStatus:
    if item_type not in ELIGIBLE_PLANNER_TYPES:
        return PlannerStatus.NOT_REQUIRED
    if status in OPEN_WORK_ITEM_STATUSES:
        return PlannerStatus.NEEDS_TRANSFER
    return PlannerStatus.NO_LONGER_RELEVANT
