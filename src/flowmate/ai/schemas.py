from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
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
    CONDITIONAL = "conditional"


class DraftReadiness(StrEnum):
    READY = "ready"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNRESOLVED = "unresolved"


class StrictDraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DraftInputContext(StrictDraftModel):
    current_datetime: AwareDatetime
    timezone: NonEmptyText
    active_workspace: NonEmptyText
    channel: Literal["telegram"]
    source: DraftSource


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
        if self.relation in {DependencyRelation.BEFORE, DependencyRelation.AFTER}:
            if self.target_item_number is None:
                raise ValueError("sequential dependency requires a target item")
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

    @field_validator("reminder_candidate")
    @classmethod
    def reject_resolved_reminder_without_time(
        cls,
        value: TemporalCandidate | None,
    ) -> TemporalCandidate | None:
        if (
            value is not None
            and value.status is TemporalStatus.RESOLVED
            and not value.time_was_explicit
        ):
            raise ValueError("reminder without an explicit time must be ambiguous")
        return value


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


class DraftItemAssessment(StrictDraftModel):
    item: DraftItem
    readiness: DraftReadiness


class DraftAnalysisResult(StrictDraftModel):
    context: DraftInputContext
    overall_intent: DraftItemType
    items: list[DraftItemAssessment] = Field(min_length=1)
    ambiguities: list[NonEmptyText]
    confidence: float = Field(ge=0.0, le=1.0)
