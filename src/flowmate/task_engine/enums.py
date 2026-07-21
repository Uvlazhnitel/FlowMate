from enum import StrEnum


class WorkItemType(StrEnum):
    TASK = "task"
    FOLLOW_UP = "follow_up"
    WAITING = "waiting"
    QUESTION = "question"
    DECISION = "decision"
    AGENDA_ITEM = "agenda_item"


class WorkItemStatus(StrEnum):
    INBOX = "inbox"
    PLANNED = "planned"
    ACTIVE = "active"
    WAITING = "waiting"
    SNOOZED = "snoozed"
    DONE = "done"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class WorkItemPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class WorkItemRelationType(StrEnum):
    RELATED_TO = "related_to"
    BLOCKED_BY = "blocked_by"
    AFTER_COMPLETION = "after_completion"
    CREATED_FROM = "created_from"
    WAITING_FOR = "waiting_for"


class WorkItemEventType(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    STATUS_CHANGED = "status_changed"
    LINKED = "linked"


class NoteTargetType(StrEnum):
    WORK_ITEM = "work_item"
    PERSON = "person"
    TOPIC = "topic"
