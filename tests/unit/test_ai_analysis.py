from datetime import datetime
from zoneinfo import ZoneInfo

from flowmate.ai.analysis import build_analysis_result
from flowmate.ai.schemas import (
    DependencyCandidate,
    DependencyRelation,
    DraftReadiness,
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


def test_global_ambiguity_prevents_ready_status() -> None:
    result = make_parse_result(ambiguities=["Неясен владелец"])

    analysis = build_analysis_result(
        result,
        context=make_context(),
        high_threshold=0.8,
        clarification_threshold=0.5,
    )

    assert analysis.items[0].readiness is DraftReadiness.CLARIFICATION_REQUIRED


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
