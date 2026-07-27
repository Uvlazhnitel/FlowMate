from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from flowmate.ai.schemas import DraftItemType
from flowmate.bot.handlers.drafts import (
    format_conversion_summary,
    format_draft_summary,
)
from flowmate.bot.presentation import (
    TelegramDisplayContext,
    format_datetime,
    html_text,
    join_html_blocks,
)
from flowmate.db.models import WorkItem
from flowmate.task_engine.conversion import DraftConversionResult
from tests.ai_factories import (
    make_analysis_result,
    make_context,
    make_draft_item,
    make_parse_result,
    make_temporal_candidate,
)


def test_natural_dates_respect_timezone_and_display_preferences() -> None:
    context = TelegramDisplayContext(ZoneInfo("Europe/Riga"))
    now = datetime(2026, 7, 27, 8, tzinfo=UTC)

    assert (
        format_datetime(datetime(2026, 7, 27, 8, 14, tzinfo=UTC), context, now=now)
        == "Сегодня, 11:14"
    )
    assert (
        format_datetime(datetime(2026, 7, 28, 6, tzinfo=UTC), context, now=now)
        == "Завтра, 09:00"
    )
    assert (
        format_datetime(
            datetime(2026, 8, 7, 20, 59, 59, tzinfo=UTC),
            context,
            now=now,
            date_only=True,
        )
        == "7 августа"
    )

    alternate = TelegramDisplayContext(
        ZoneInfo("Europe/Riga"),
        date_display_format="year_month_day",
        time_display_format="12h",
    )
    assert (
        format_datetime(datetime(2027, 8, 7, 18, tzinfo=UTC), alternate, now=now)
        == "2027-08-07, 9:00 PM"
    )


def test_draft_card_hides_internal_ai_metadata_and_escapes_user_text() -> None:
    due = make_temporal_candidate(
        original_phrase="через час",
        normalized_value=datetime(2026, 8, 7, 8, 14, tzinfo=UTC),
    )
    analysis = make_analysis_result(
        make_parse_result(
            [
                make_draft_item(
                    title="Отправить <список> & подтвердить",
                    description="Внутреннее описание",
                    person_candidates=["Анкур", "Акаш"],
                    topic_candidates=["ежемесячное признание"],
                    due_date_candidate=due,
                    missing_fields=["amount"],
                    confidence=0.98,
                )
            ],
            confidence=0.98,
        ),
        context=make_context(timezone="Europe/Riga"),
    )

    card = format_draft_summary(
        analysis,
        display=TelegramDisplayContext(ZoneInfo("Europe/Riga")),
    )

    assert card.startswith("📝 <b>Проверьте запись</b>\n\n📌 <b>Задача</b>")
    assert "Отправить &lt;список&gt; &amp; подтвердить" in card
    assert "Анкур" not in card
    assert "ежемесячное признание" not in card
    assert "Уверенность" not in card
    assert "missing" not in card
    assert "2026-08-07T" not in card


def test_confirmation_lists_only_created_records_without_zero_counters() -> None:
    item = WorkItem(
        id=uuid4(),
        user_id=uuid4(),
        workspace="work",
        type="task",
        title="Отправить список кандидатов",
        status="active",
        priority="normal",
        due_at=datetime(2026, 8, 7, 20, 59, 59, tzinfo=UTC),
    )
    result = DraftConversionResult(
        draft_id=uuid4(),
        work_items=(item,),
        notes=(),
        counts={DraftItemType.TASK: 1},
    )

    text = format_conversion_summary(
        result,
        display=TelegramDisplayContext(ZoneInfo("Europe/Riga")),
        workspace="work",
    )

    assert text == (
        "✅ <b>Задача создана</b>\n\n"
        "📌 <b>Задача</b>\n"
        "Отправить список кандидатов\n"
        "📅 7 августа\n\n"
        "📂 Работа"
    )
    assert "follow-up" not in text
    assert "— 0" not in text


def test_html_and_message_limits_are_safe() -> None:
    assert html_text("<b>Tom & Jerry</b>") == "&lt;b&gt;Tom &amp; Jerry&lt;/b&gt;"
    chunks = join_html_blocks(["a" * 2500, "b" * 2500])
    assert len(chunks) == 2
    assert all(len(chunk) <= 4000 for chunk in chunks)
