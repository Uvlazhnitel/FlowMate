from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DraftItemType(StrEnum):
    TASK = "task"
    FOLLOW_UP = "follow_up"
    WAITING = "waiting"
    QUESTION = "question"
    NOTE = "note"
    DECISION = "decision"
    AGENDA_ITEM = "agenda_item"
    UNKNOWN = "unknown"


class DraftSource(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class TemporalStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"


class DependencyRelation(StrEnum):
    BEFORE = "before"
    AFTER = "after"
    BLOCKED_BY = "blocked_by"
    WAITING_FOR = "waiting_for"
    CONDITIONAL = "conditional"


class DraftReadiness(StrEnum):
    READY = "ready"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNRESOLVED = "unresolved"


class ManagementAction(StrEnum):
    COMPLETE = "complete"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"
    REOPEN = "reopen"
    WAITING_RECEIVED = "waiting_received"
    ADD_NOTE = "add_note"
    CHANGE_TOPIC = "change_topic"
    ADD_PERSON = "add_person"
    REPLACE_PERSON = "replace_person"
    SHOW_DETAILS = "show_details"


class SearchWorkItemType(StrEnum):
    TASK = "task"
    FOLLOW_UP = "follow_up"
    WAITING = "waiting"
    QUESTION = "question"
    DECISION = "decision"
    AGENDA_ITEM = "agenda_item"


class SearchWorkItemStatus(StrEnum):
    INBOX = "inbox"
    PLANNED = "planned"
    ACTIVE = "active"
    WAITING = "waiting"
    SNOOZED = "snoozed"
    DONE = "done"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class StrictDraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MeetingDraftContext(StrictDraftModel):
    meeting_id: UUID
    meeting_type: Literal[
        "lead", "team", "client_sync", "steering", "one_to_one", "other"
    ]
    participants: list[NonEmptyText]
    topics: list[NonEmptyText]
    primary_topic: NonEmptyText | None = None


class DraftInputContext(StrictDraftModel):
    current_datetime: AwareDatetime
    timezone: NonEmptyText
    active_workspace: NonEmptyText
    channel: Literal["telegram"]
    source: DraftSource
    meeting: MeetingDraftContext | None = None


class MeetingReviewProposal(StrictDraftModel):
    source_capture_id: UUID
    source_draft_item_id: UUID | None = None
    category: Literal[
        "task",
        "follow_up",
        "waiting",
        "answered_question",
        "unresolved_question",
        "note",
        "decision",
        "agenda_item",
    ]
    item: "DraftItem"
    suggested_next_action: NonEmptyText | None = None
    clarification_question: NonEmptyText | None = None
    consequences: list[NonEmptyText] = Field(default_factory=list, max_length=20)
    related_proposal_numbers: list[int] = Field(default_factory=list, max_length=50)


class MeetingAgendaSuggestion(StrictDraftModel):
    work_item_id: UUID
    outcome: Literal["pending", "discussed", "answered", "deferred", "unresolved"]
    result: NonEmptyText | None = None


class MeetingReviewParseResult(StrictDraftModel):
    summary: NonEmptyText
    proposals: list[MeetingReviewProposal] = Field(max_length=500)
    agenda: list[MeetingAgendaSuggestion] = Field(default_factory=list, max_length=500)
    suggested_next_actions: list[NonEmptyText] = Field(
        default_factory=list, max_length=50
    )


class TemporalCandidate(StrictDraftModel):
    original_phrase: NonEmptyText
    normalized_value: AwareDatetime | None
    status: TemporalStatus
    explanation: NonEmptyText | None
    time_was_explicit: bool

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        if self.status is TemporalStatus.RESOLVED:
            if self.normalized_value is None:
                raise ValueError("resolved temporal candidate requires a value")
            return self
        if self.normalized_value is not None:
            raise ValueError(
                "ambiguous or invalid temporal candidate cannot have a value"
            )
        if self.explanation is None:
            raise ValueError("unresolved temporal candidate requires an explanation")
        return self


class DependencyCandidate(StrictDraftModel):
    relation: DependencyRelation
    original_phrase: NonEmptyText
    target_item_number: int | None = Field(ge=1)
    condition: NonEmptyText | None

    @model_validator(mode="after")
    def validate_relation_fields(self) -> Self:
        if self.relation in {
            DependencyRelation.BEFORE,
            DependencyRelation.AFTER,
            DependencyRelation.BLOCKED_BY,
            DependencyRelation.WAITING_FOR,
        }:
            if self.target_item_number is None:
                raise ValueError("work item dependency requires a target item")
        elif self.condition is None:
            raise ValueError("conditional dependency requires a condition")
        return self


class DraftItem(StrictDraftModel):
    type: DraftItemType
    title: NonEmptyText
    description: NonEmptyText | None
    person_candidates: list[NonEmptyText]
    topic_candidates: list[NonEmptyText]
    due_date_candidate: TemporalCandidate | None
    reminder_candidate: TemporalCandidate | None
    notes: list[NonEmptyText]
    missing_fields: list[NonEmptyText]
    ambiguities: list[NonEmptyText]
    dependencies: list[DependencyCandidate]
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def normalize_day_level_reminder(self) -> Self:
        value = self.reminder_candidate
        if (
            value is not None
            and value.status is TemporalStatus.RESOLVED
            and not value.time_was_explicit
        ):
            # A date-only "remind me" request is a day-level deadline. The daily
            # digest provides the early notification without inventing an exact time.
            due_date = self.due_date_candidate
            if due_date is None or due_date.status is not TemporalStatus.RESOLVED:
                due_date = value
            return self.model_copy(
                update={
                    "due_date_candidate": due_date,
                    "reminder_candidate": None,
                }
            )
        return self


class DraftParseResult(StrictDraftModel):
    overall_intent: DraftItemType
    draft_items: list[DraftItem] = Field(min_length=1)
    ambiguities: list[NonEmptyText]
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_items_and_dependencies(self) -> Self:
        item_count = len(self.draft_items)
        if item_count == 0:
            raise ValueError("at least one draft item is required")
        for item_number, item in enumerate(self.draft_items, start=1):
            for dependency in item.dependencies:
                target = dependency.target_item_number
                if target is not None and target > item_count:
                    raise ValueError("dependency target is outside the draft result")
                if target == item_number:
                    raise ValueError("draft item cannot depend on itself")
        return self


class ManagementIntent(StrictDraftModel):
    action: ManagementAction
    target_type: DraftItemType | None
    record_query: NonEmptyText | None
    contextual_reference: bool
    person_candidate: NonEmptyText | None
    topic_candidate: NonEmptyText | None
    note_text: NonEmptyText | None
    temporal_candidate: TemporalCandidate | None
    missing_fields: list[NonEmptyText]
    ambiguities: list[NonEmptyText]
    confidence: float = Field(ge=0.0, le=1.0)


class SearchIntent(StrictDraftModel):
    text_query: NonEmptyText | None
    person_query: NonEmptyText | None
    topic_query: NonEmptyText | None
    item_types: list[SearchWorkItemType]
    statuses: list[SearchWorkItemStatus]
    include_all_statuses: bool
    due_from: AwareDatetime | None
    due_to: AwareDatetime | None
    overdue: bool
    stale_contacts: bool
    ambiguities: list[NonEmptyText]
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_filters(self) -> Self:
        if self.include_all_statuses and self.statuses:
            raise ValueError("all statuses cannot be combined with explicit statuses")
        if self.due_from is not None and self.due_to is not None:
            if self.due_from >= self.due_to:
                raise ValueError("search date range must be increasing")
        if self.overdue and (self.due_from is not None or self.due_to is not None):
            raise ValueError("overdue cannot be combined with a date range")
        return self


class TelegramTextParseResult(StrictDraftModel):
    mode: Literal["new_draft", "management", "search"]
    draft: DraftParseResult | None = None
    management: ManagementIntent | None = None
    search: SearchIntent | None = None

    @model_validator(mode="after")
    def validate_mode_payload(self) -> Self:
        payloads = {
            "new_draft": self.draft,
            "management": self.management,
            "search": self.search,
        }
        if payloads[self.mode] is not None:
            if sum(value is not None for value in payloads.values()) == 1:
                return self
        raise ValueError("text parse mode must have exactly one matching payload")


class SnoozeTimeParseResult(StrictDraftModel):
    original_phrase: NonEmptyText
    normalized_value: AwareDatetime
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguities: list[NonEmptyText]


class DraftItemAssessment(StrictDraftModel):
    item: DraftItem
    readiness: DraftReadiness


class DraftAnalysisResult(StrictDraftModel):
    context: DraftInputContext
    overall_intent: DraftItemType
    items: list[DraftItemAssessment] = Field(min_length=1)
    ambiguities: list[NonEmptyText]
    confidence: float = Field(ge=0.0, le=1.0)


MeetingReviewProposal.model_rebuild()
