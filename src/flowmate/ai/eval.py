import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from flowmate.ai.analysis import build_analysis_result
from flowmate.ai.prompt import build_system_prompt, build_text_routing_prompt
from flowmate.ai.prompt_versions import PROMPT_VERSIONS
from flowmate.ai.schemas import DraftInputContext, DraftParseResult, DraftSource
from flowmate.ai.service import parse_exact_local_date


def fixture_path() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "ai_eval.json"


def run_evaluation() -> tuple[int, int]:
    raw = json.loads(fixture_path().read_text(encoding="utf-8"))
    failures: list[str] = []
    for case in raw:
        kind = case.get("kind", "recorded_parse")
        if kind == "exact_date":
            context = DraftInputContext(
                current_datetime=datetime.fromisoformat(case["current_datetime"]),
                timezone=case["timezone"],
                active_workspace="anonymized-evaluation",
                channel="telegram",
                source=DraftSource.VOICE,
            )
            candidate = parse_exact_local_date(case["input"], context)
            actual = (
                candidate.normalized_value.isoformat()
                if candidate is not None and candidate.normalized_value is not None
                else None
            )
            if actual != case["expected"]:
                failures.append(f"{case['id']}: exact date")
            continue
        if kind == "invalid_provider_output":
            try:
                DraftParseResult.model_validate(case["recorded_output"])
            except ValidationError:
                continue
            failures.append(f"{case['id']}: invalid output accepted")
            continue
        if kind == "meeting_context":
            context = DraftInputContext.model_validate_json(
                json.dumps(case["context"], ensure_ascii=False)
            )
            prompt = build_system_prompt(context)
            if any(value not in prompt for value in case["expected_fragments"]):
                failures.append(f"{case['id']}: meeting context")
            continue
        parsed = DraftParseResult.model_validate_json(
            json.dumps(case["recorded_output"], ensure_ascii=False)
        )
        actual_types = [item.type.value for item in parsed.draft_items]
        if actual_types != case["expected_types"]:
            failures.append(f"{case['id']}: types")
        first = parsed.draft_items[0]
        actual_due = (
            first.due_date_candidate.normalized_value.isoformat()
            if first.due_date_candidate is not None
            and first.due_date_candidate.normalized_value is not None
            else None
        )
        actual_reminder = (
            first.reminder_candidate.normalized_value.isoformat()
            if first.reminder_candidate is not None
            and first.reminder_candidate.normalized_value is not None
            else None
        )
        if actual_due != case["expected_due"]:
            failures.append(f"{case['id']}: due")
        if actual_reminder != case["expected_reminder"]:
            failures.append(f"{case['id']}: reminder")
        if "expected_readiness" in case:
            evaluation_context = DraftInputContext(
                current_datetime=datetime(
                    2026, 7, 22, 12, tzinfo=ZoneInfo("Europe/Riga")
                ),
                timezone="Europe/Riga",
                active_workspace="anonymized-evaluation",
                channel="telegram",
                source=DraftSource.VOICE,
            )
            analysis = build_analysis_result(
                parsed,
                context=evaluation_context,
                high_threshold=0.8,
                clarification_threshold=0.5,
            )
            actual_readiness = [item.readiness.value for item in analysis.items]
            if actual_readiness != case["expected_readiness"]:
                failures.append(f"{case['id']}: readiness")

    timezone = ZoneInfo("Europe/Riga")
    context = DraftInputContext(
        current_datetime=datetime(2026, 7, 22, 12, tzinfo=timezone),
        timezone=timezone.key,
        active_workspace="anonymized-evaluation",
        channel="telegram",
        source=DraftSource.VOICE,
    )
    draft_prompt = build_system_prompt(context)
    routing_prompt = build_text_routing_prompt(context)
    if len(routing_prompt) >= len(draft_prompt):
        failures.append("routing prompt was not reduced")
    if "23:59:59" not in draft_prompt or "23:59:59" not in routing_prompt:
        failures.append("date-only policy missing from prompt")
    if (
        "amounts" not in draft_prompt.casefold()
        or "optional" not in routing_prompt.casefold()
    ):
        failures.append("optional-field policy missing from prompt")
    if len(set(PROMPT_VERSIONS.values())) != len(PROMPT_VERSIONS):
        failures.append("prompt versions are not independently versioned")
    if failures:
        raise SystemExit("offline AI evaluation failed: " + ", ".join(failures))
    return len(raw), len(raw)


def main() -> None:
    passed, total = run_evaluation()
    print(f"offline AI evaluation: {passed}/{total} fixtures passed")
    print("network calls: 0")


if __name__ == "__main__":
    main()
