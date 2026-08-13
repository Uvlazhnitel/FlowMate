# ruff: noqa: RUF001
from datetime import datetime
from zoneinfo import ZoneInfo

from flowmate.ai.analysis import (
    apply_item_type_policy,
    apply_itemization_policy,
    build_analysis_result,
    explicit_itemization_decision,
    explicit_task_segments,
    propagate_shared_leading_due_date,
)
from flowmate.ai.schemas import (
    DependencyCandidate,
    DependencyRelation,
    DraftItemType,
    DraftReadiness,
    ItemizationBasis,
    ItemizationDecision,
    TemporalStatus,
)
from tests.ai_factories import (
    make_context,
    make_draft_item,
    make_parse_result,
    make_temporal_candidate,
)


def test_date_only_due_is_normalized_to_local_end_of_day() -> None:
    due = make_temporal_candidate(
        original_phrase="завтра",
        normalized_value=datetime.fromisoformat("2026-07-21T00:00:00+03:00"),
        time_was_explicit=False,
    )
    result = make_parse_result([make_draft_item(due_date_candidate=due)])
    context = make_context(timezone="Europe/Riga")

    analysis = build_analysis_result(
        result,
        context=context,
        high_threshold=0.8,
        clarification_threshold=0.5,
    )

    normalized = analysis.items[0].item.due_date_candidate
    assert normalized is not None
    assert normalized.normalized_value == datetime(
        2026,
        7,
        21,
        23,
        59,
        59,
        tzinfo=ZoneInfo("Europe/Riga"),
    )


def test_confidence_and_semantic_issues_determine_readiness() -> None:
    ambiguous = make_temporal_candidate(
        status=TemporalStatus.AMBIGUOUS,
        normalized_value=None,
        explanation="Time is missing",
        time_was_explicit=False,
    )
    invalid = make_temporal_candidate(
        status=TemporalStatus.INVALID,
        normalized_value=None,
        explanation="Date does not exist",
    )
    result = make_parse_result(
        [
            make_draft_item(title="Ready", confidence=0.8),
            make_draft_item(title="Medium", confidence=0.79),
            make_draft_item(title="Low", confidence=0.49),
            make_draft_item(
                title="High but ambiguous",
                confidence=0.95,
                reminder_candidate=ambiguous,
            ),
            make_draft_item(
                title="Invalid date",
                confidence=0.95,
                due_date_candidate=invalid,
            ),
        ]
    )

    analysis = build_analysis_result(
        result,
        context=make_context(),
        high_threshold=0.8,
        clarification_threshold=0.5,
    )

    assert [assessment.readiness for assessment in analysis.items] == [
        DraftReadiness.READY,
        DraftReadiness.CLARIFICATION_REQUIRED,
        DraftReadiness.UNRESOLVED,
        DraftReadiness.CLARIFICATION_REQUIRED,
        DraftReadiness.UNRESOLVED,
    ]


def test_optional_missing_fields_and_general_ambiguity_do_not_block_capture() -> None:
    result = make_parse_result(
        [
            make_draft_item(
                title="Скинуть деньги за Польшу",
                missing_fields=["сумма", "дата", "тема"],
                ambiguities=["Сумма не указана"],
                confidence=0.92,
            )
        ],
        ambiguities=["Неясна сумма"],
    )

    analysis = build_analysis_result(
        result,
        context=make_context(),
        high_threshold=0.8,
        clarification_threshold=0.5,
    )

    assert analysis.items[0].readiness is DraftReadiness.READY
    assert analysis.items[0].item.missing_fields == ["сумма", "дата", "тема"]


def test_explicit_identity_ambiguity_does_not_block_capture() -> None:
    result = make_parse_result(
        [
            make_draft_item(
                person_candidates=["Анна", "Анна П."],
                ambiguities=["Неясно, какая Анна"],
                confidence=0.95,
            )
        ]
    )

    analysis = build_analysis_result(
        result,
        context=make_context(),
        high_threshold=0.8,
        clarification_threshold=0.5,
    )

    assert analysis.items[0].readiness is DraftReadiness.READY


def test_duplicates_are_merged_and_dependency_targets_are_remapped() -> None:
    after_third = DependencyCandidate(
        relation=DependencyRelation.AFTER,
        original_phrase="после заметки",
        target_item_number=3,
        condition=None,
    )
    after_first = DependencyCandidate(
        relation=DependencyRelation.AFTER,
        original_phrase="после этого",
        target_item_number=1,
        condition=None,
    )
    result = make_parse_result(
        [
            make_draft_item(
                title="Write Anton",
                person_candidates=["Антон"],
                confidence=0.9,
            ),
            make_draft_item(
                title="  write   anton ",
                person_candidates=["delivery lead"],
                notes=["Ask about dates"],
                dependencies=[after_third],
                confidence=0.7,
            ),
            make_draft_item(
                title="Record client wait",
                dependencies=[after_first],
            ),
        ]
    )

    analysis = build_analysis_result(
        result,
        context=make_context(),
        high_threshold=0.8,
        clarification_threshold=0.5,
    )

    assert len(analysis.items) == 2
    merged = analysis.items[0].item
    assert merged.person_candidates == ["Антон", "delivery lead"]
    assert merged.notes == ["Ask about dates"]
    assert merged.confidence == 0.7
    assert merged.dependencies[0].target_item_number == 2
    assert analysis.items[1].item.dependencies[0].target_item_number == 1


def test_uncertain_multiple_outcomes_collapse_to_consolidated_item() -> None:
    consolidated = make_draft_item(title="Подготовить отчёт и отправить его клиенту")
    result = make_parse_result(
        [
            make_draft_item(title="Подготовить отчёт"),
            make_draft_item(title="Отправить отчёт клиенту"),
        ],
        itemization_basis=ItemizationBasis.UNCERTAIN,
        itemization_confidence=0.89,
        consolidated_item=consolidated,
    )

    normalized = apply_itemization_policy(
        result,
        source_text="Подготовить отчёт и отправить его клиенту",
        split_threshold=0.90,
    )

    assert normalized.itemization_decision is ItemizationDecision.SINGLE
    assert normalized.draft_items == [consolidated]
    assert normalized.consolidated_item is None


def test_high_confidence_independent_outcomes_remain_multiple() -> None:
    result = make_parse_result(
        [
            make_draft_item(title="Купить молоко"),
            make_draft_item(title="Забрать посылку"),
        ],
        itemization_basis=ItemizationBasis.INDEPENDENT_OUTCOMES,
        itemization_confidence=0.90,
    )

    normalized = apply_itemization_policy(
        result,
        source_text="Купить молоко и забрать посылку",
        split_threshold=0.90,
    )

    assert normalized.itemization_decision is ItemizationDecision.MULTIPLE
    assert len(normalized.draft_items) == 2


def test_confident_multiple_does_not_require_consolidated_fallback() -> None:
    result = make_parse_result(
        [
            make_draft_item(title="Добавить людей в OrgChart"),
            make_draft_item(title="Сделать CDP refresher"),
            make_draft_item(title="Добавить людей в forecast"),
        ],
        itemization_basis=ItemizationBasis.INDEPENDENT_OUTCOMES,
        itemization_confidence=0.90,
        consolidated_item=None,
    )

    normalized = apply_itemization_policy(
        result,
        source_text="Добавить людей, затем обновить CDP, затем forecast",
        split_threshold=0.90,
    )

    assert normalized.itemization_decision is ItemizationDecision.MULTIPLE
    assert len(normalized.draft_items) == 3
    assert normalized.consolidated_item is None


def test_leading_due_date_is_shared_by_sequence_items_without_own_date() -> None:
    tomorrow = make_temporal_candidate(
        original_phrase="Завтра",
        normalized_value=datetime.fromisoformat("2026-08-14T00:00:00+03:00"),
        time_was_explicit=False,
    )
    later = make_temporal_candidate(
        original_phrase="в субботу",
        normalized_value=datetime.fromisoformat("2026-08-15T00:00:00+03:00"),
        time_was_explicit=False,
    )
    result = make_parse_result(
        [
            make_draft_item(title="Первое", due_date_candidate=tomorrow),
            make_draft_item(title="Второе"),
            make_draft_item(title="Третье", due_date_candidate=later),
        ]
    )

    normalized = propagate_shared_leading_due_date(
        result,
        source_text="Завтра сделать первое, затем второе, потом третье в субботу",
    )

    assert normalized.draft_items[1].due_date_candidate == tomorrow
    assert normalized.draft_items[2].due_date_candidate == later


def test_shared_due_date_requires_resolved_leading_phrase_and_sequence() -> None:
    ambiguous = make_temporal_candidate(
        original_phrase="после обеда",
        normalized_value=None,
        status=TemporalStatus.AMBIGUOUS,
        explanation="Неясное время",
        time_was_explicit=False,
    )
    result = make_parse_result(
        [
            make_draft_item(title="Первое", due_date_candidate=ambiguous),
            make_draft_item(title="Второе"),
        ]
    )

    assert (
        propagate_shared_leading_due_date(
            result,
            source_text="После обеда сделать первое, затем второе",
        )
        .draft_items[1]
        .due_date_candidate
        is None
    )
    resolved = make_temporal_candidate(
        original_phrase="завтра",
        normalized_value=datetime.fromisoformat("2026-08-14T00:00:00+03:00"),
        time_was_explicit=False,
    )
    without_leading_date = make_parse_result(
        [
            make_draft_item(title="Первое", due_date_candidate=resolved),
            make_draft_item(title="Второе"),
        ]
    )
    assert (
        propagate_shared_leading_due_date(
            without_leading_date,
            source_text="Сделать первое завтра, затем второе",
        )
        .draft_items[1]
        .due_date_candidate
        is None
    )


def test_explicit_single_directive_overrides_confident_split() -> None:
    consolidated = make_draft_item(title="Подготовить и отправить отчёт")
    result = make_parse_result(
        [
            make_draft_item(title="Подготовить отчёт"),
            make_draft_item(title="Отправить отчёт"),
        ],
        itemization_basis=ItemizationBasis.SEPARATE_SENTENCES,
        itemization_confidence=0.99,
        consolidated_item=consolidated,
    )

    normalized = apply_itemization_policy(
        result,
        source_text=("Подготовить отчёт. Отправить его клиенту. Не разделяй."),
        split_threshold=0.90,
    )

    assert normalized.draft_items == [consolidated]


def test_explicit_multiple_directive_overrides_low_split_confidence() -> None:
    result = make_parse_result(
        [
            make_draft_item(title="Добавить Личи Home Office"),
            make_draft_item(title="Отправить сообщение клиенту"),
        ],
        itemization_basis=ItemizationBasis.UNCERTAIN,
        itemization_confidence=0.60,
    )

    normalized = apply_itemization_policy(
        result,
        source_text=(
            "Сделай две задачи: добавить Личи Home Office и отправить сообщение клиенту"
        ),
        split_threshold=0.90,
    )

    assert normalized.itemization_decision is ItemizationDecision.MULTIPLE
    assert len(normalized.draft_items) == 2


def test_voice_task_markers_create_four_explicit_segments() -> None:
    transcript = (
        "Нужно поменять VP3 на VP4. И сегодня еще одна задача. "
        "Проверить количество дней в forecast для ЛИЧ. Другая задача. "
        "Включиться в импорт и отсортировать важную задачу. "
        "Это ещё одна задача. Посмотреть, что хотели лиды в чате."
    )

    segments = explicit_task_segments(transcript)

    assert len(segments) == 4
    assert segments[0] == "Нужно поменять VP3 на VP4"
    assert segments[1].startswith("сегодня Проверить количество дней")
    assert segments[2].startswith("Включиться в импорт")
    assert segments[3].startswith("Посмотреть, что хотели лиды")
    assert explicit_itemization_decision(transcript) is ItemizationDecision.MULTIPLE


def test_numbered_tasks_can_share_one_line() -> None:
    segments = explicit_task_segments(
        "1. Купить молоко 2. Забрать посылку 3. Позвонить Антону"
    )

    assert segments == ("Купить молоко", "Забрать посылку", "Позвонить Антону")


def test_spoken_markers_do_not_require_reliable_punctuation() -> None:
    segments = explicit_task_segments(
        "Сделать первое еще одна: сделать второе другая задача сделать третье"
    )

    assert segments == ("Сделать первое", "сделать второе", "сделать третье")


def test_single_item_directive_wins_over_boundaries() -> None:
    assert not explicit_task_segments(
        "Одна задача: подготовить отчёт. Другая задача: отправить его клиенту"
    )
    assert not explicit_task_segments(
        "Подготовить отчёт. Следующая задача: отправить его. Не разделяй"
    )


def test_multiple_verbs_without_boundaries_are_not_explicit_segments() -> None:
    assert not explicit_task_segments("Подготовить и отправить отчёт клиенту")


def test_explicit_follow_up_is_corrected_when_provider_returns_task() -> None:
    result = make_parse_result(
        [
            make_draft_item(
                title="Проверить ответ Антона",
                person_candidates=["Антон"],
            )
        ]
    )

    normalized = apply_item_type_policy(
        result,
        source_text="Сделать фоллоу-ап: проверить, ответил ли Антон",
    )

    assert normalized.overall_intent is DraftItemType.FOLLOW_UP
    assert normalized.draft_items[0].type is DraftItemType.FOLLOW_UP


def test_direct_call_with_person_is_corrected_to_follow_up() -> None:
    result = make_parse_result(
        [make_draft_item(title="Позвонить Лене", person_candidates=["Лена"])]
    )

    normalized = apply_item_type_policy(result, source_text="Позвонить Лене")

    assert normalized.draft_items[0].type is DraftItemType.FOLLOW_UP


def test_reminder_and_person_deliverable_remain_tasks() -> None:
    reminder = make_parse_result([make_draft_item(title="Отправить отчёт Антону")])
    deliverable = make_parse_result(
        [
            make_draft_item(
                title="Подготовить отчёт для Антона",
                person_candidates=["Антон"],
            )
        ]
    )

    reminder_result = apply_item_type_policy(
        reminder,
        source_text="Напомни завтра отправить отчёт Антону",
    )
    deliverable_result = apply_item_type_policy(
        deliverable,
        source_text="Подготовить отчёт для Антона",
    )

    assert reminder_result.draft_items[0].type is DraftItemType.TASK
    assert deliverable_result.draft_items[0].type is DraftItemType.TASK


def test_multi_item_source_marker_does_not_reclassify_unrelated_task() -> None:
    result = make_parse_result(
        [
            make_draft_item(title="Написать Антону", person_candidates=["Антон"]),
            make_draft_item(title="Купить молоко"),
        ]
    )

    normalized = apply_item_type_policy(
        result,
        source_text="Фоллоу-ап Антону и купить молоко",
    )

    assert [item.type for item in normalized.draft_items] == [
        DraftItemType.TASK,
        DraftItemType.TASK,
    ]
