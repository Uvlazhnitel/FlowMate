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
    item_count: int = 1,
) -> ClarificationQuestion | None:
    item = assessment.item
    for field, label, candidate in (
        ("due_date", "срок", item.due_date_candidate),
        ("reminder", "напоминание", item.reminder_candidate),
    ):
        if candidate is not None and candidate.status is not TemporalStatus.RESOLVED:
            text = (
                f"Пункт {position} из {item_count}: уточните {label} "
                f"для «{item.title}»."
                if item_count > 1
                else f"Уточните {label} для «{item.title}»."
            )
            return ClarificationQuestion(
                text=text,
                context=item_context(position, field),
            )
    return None


def question_for_item(
    assessment: DraftItemAssessment,
    position: int,
    item_count: int = 1,
) -> ClarificationQuestion | None:
    item = assessment.item
    temporal = question_for_temporal(assessment, position, item_count)
    if temporal is not None:
        return temporal
    if item.type is DraftItemType.UNKNOWN:
        text = (
            f"Пункт {position} из {item_count}: что это за запись: «{item.title}»?"
            if item_count > 1
            else f"Что это за запись: «{item.title}»?"
        )
        return ClarificationQuestion(
            text=text,
            context=item_context(position, "type"),
            options=(
                QuestionOption("Задача", "это задача"),
                QuestionOption("Заметка", "это заметка"),
                QuestionOption("Вопрос", "это вопрос"),
            ),
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
    item_count = len(analysis.items)
    for position, assessment in enumerate(analysis.items, start=1):
        question = question_for_item(assessment, position, item_count)
        if question is not None and question.context["field"] != "confidence":
            return question
    if item_count > 1 and any(
        assessment.readiness is not DraftReadiness.READY
        for assessment in analysis.items
    ):
        return ClarificationQuestion(
            text=f"Сохранить все {item_count} записи?",
            context={"field": "confidence_all", "item_count": item_count},
            options=(
                QuestionOption(
                    f"Сохранить все {item_count}",
                    "сохрани все",
                    "confirm",
                ),
                QuestionOption("Изменить список", "изменить список", "change"),
                QuestionOption("Отменить", "отмена", "cancel"),
            ),
        )
    if item_count == 1:
        return question_for_item(analysis.items[0], 1)
    return None
