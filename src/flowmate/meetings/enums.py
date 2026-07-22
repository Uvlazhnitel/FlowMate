from enum import StrEnum


class MeetingType(StrEnum):
    LEAD = "lead"
    TEAM = "team"
    CLIENT_SYNC = "client_sync"
    STEERING = "steering"
    ONE_TO_ONE = "one_to_one"
    OTHER = "other"


class MeetingStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    PROCESSING = "processing"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MeetingEventType(StrEnum):
    CREATED = "created"
    STARTED = "started"
    ENDED = "ended"
    CANCELLED = "cancelled"
    REVIEW_GENERATED = "review_generated"
    REVIEW_FAILED = "review_failed"
    CLARIFICATION_ANSWERED = "clarification_answered"
    CONVERTED = "converted"
    COMPLETED = "completed"
    AGENDA_UPDATED = "agenda_updated"
