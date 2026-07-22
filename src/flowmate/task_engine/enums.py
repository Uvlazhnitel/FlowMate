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
    COMPLETED = "completed"
    REOPENED = "reopened"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"
    NOTE_ADDED = "note_added"
    TOPIC_CHANGED = "topic_changed"
    PERSON_CHANGED = "person_changed"
    WAITING_RECEIVED = "waiting_received"
    PERSON_REPLIED = "person_replied"
    REMINDER_SNOOZED = "reminder_snoozed"
    ARCHIVED = "archived"


class WorkItemAction(StrEnum):
    SELECT_RECORD = "select_record"
    RESCHEDULE = "reschedule"
    ADD_NOTE = "add_note"
    CHANGE_TOPIC = "change_topic"
    ADD_PERSON = "add_person"
    REPLACE_PERSON = "replace_person"
    REMINDER_SNOOZE = "reminder_snooze"
    DIGEST_REVIEW = "digest_review"
    SEARCH = "search"


class NoteTargetType(StrEnum):
    WORK_ITEM = "work_item"
    PERSON = "person"
    TOPIC = "topic"
