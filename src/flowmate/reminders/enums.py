from enum import StrEnum


class ReminderType(StrEnum):
    DEADLINE = "deadline"
    FOLLOW_UP = "follow_up"
    WAITING = "waiting"
    MORNING_DIGEST = "morning_digest"
    EVENING_DIGEST = "evening_digest"
    CUSTOM = "custom"


class ReminderStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    SNOOZED = "snoozed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    DELIVERY_UNKNOWN = "delivery_unknown"


class ReminderScheduleKind(StrEnum):
    MANUAL = "manual"
    EXACT = "exact"
    BEFORE_DEADLINE = "before_deadline"
    SNOOZE = "snooze"
