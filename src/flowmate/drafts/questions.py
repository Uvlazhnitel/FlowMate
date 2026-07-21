from dataclasses import asdict, dataclass

from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftItemAssessment,
    DraftItemType,
    DraftReadiness,
    TemporalStatus,
)


@dataclass(frozen=True, slots=True)
class QuestionOption:
    label: str
    value: str
    action: str = "refine"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ClarificationQuestion:
    text: str
    context: dict[str, object]
    options: tuple[QuestionOption, ...] = ()


def item_context(position: int, field: str) -> dict[str, object]:
    return {"item_number": position, "field": field}


def question_for_temporal(
    assessment: DraftItemAssessment,
    position: int,
) -> ClarificationQuestion | None:
    item = assessment.item
    for field, label, candidate in (
        ("due_date", "срок", item.due_date_candidate),
        ("reminder", "напоминание", item.reminder_candidate),
    ):
        if candidate is not None and candidate.status is not TemporalStatus.RESOLVED:
            return ClarificationQuestion(
                text=f"Уточните {label} для «{item.title}».",
                context=item_context(position, field),
            )
    return None


def question_for_item(
    assessment: DraftItemAssessment,
    position: int,
) -> ClarificationQuestion | None:
    item = assessment.item
    temporal = question_for_temporal(assessment, position)
    if temporal is not None:
        return temporal
    if item.type is DraftItemType.UNKNOWN:
        return ClarificationQuestion(
            text=f"Что это за запись: «{item.title}»?",
            context=item_context(position, "type"),
            options=(
                QuestionOption("Задача", "это задача"),
                QuestionOption("Заметка", "это заметка"),
                QuestionOption("Вопрос", "это вопрос"),
            ),
        )
    if item.missing_fields:
        missing = item.missing_fields[0]
        return ClarificationQuestion(
            text=f"Уточните «{missing}» для «{item.title}».",
            context=item_context(position, "missing_field"),
        )
    if len(item.person_candidates) in {2, 3, 4} and item.ambiguities:
        return ClarificationQuestion(
            text=f"С кем связано: {item.title}?",  # noqa: RUF001
            context=item_context(position, "person"),
            options=tuple(
                QuestionOption(candidate, f"выбран человек: {candidate}")
                for candidate in item.person_candidates
            ),
        )
    if item.ambiguities:
        return ClarificationQuestion(
            text=f"Уточните для «{item.title}»: {item.ambiguities[0]}",
            context=item_context(position, "ambiguity"),
        )
    if assessment.readiness is not DraftReadiness.READY:
        return ClarificationQuestion(
            text=f"Все верно для: {item.title}?",  # noqa: RUF001
            context=item_context(position, "confidence"),
            options=(
                QuestionOption("Сохранить как есть", "сохрани как есть", "confirm"),
                QuestionOption("Изменить", "изменить", "change"),
            ),
        )
    return None


def next_clarification_question(
    analysis: DraftAnalysisResult,
) -> ClarificationQuestion | None:
    for position, assessment in enumerate(analysis.items, start=1):
        question = question_for_item(assessment, position)
        if question is not None:
            return question
    if analysis.ambiguities:
        return ClarificationQuestion(
            text=f"Уточните: {analysis.ambiguities[0]}",
            context={"field": "result_ambiguity"},
        )
    return None
