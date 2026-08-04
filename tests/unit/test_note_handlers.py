# ruff: noqa: RUF001
import logging
from datetime import UTC, datetime, time
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import Chat, Message, Update, User
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.errors import AIProviderError
from flowmate.ai.schemas import (
    DependencyCandidate,
    DependencyRelation,
    DraftAnalysisResult,
    DraftItemType,
    SearchIntent,
    SearchWorkItemType,
)
from flowmate.ai.service import DraftParsingService
from flowmate.bot.handlers.drafts import (
    DRAFT_ANALYZING_MESSAGE,
    DRAFT_FAILED_MESSAGE,
    DRAFT_RETRY_MESSAGE,
    apply_default_reminder_time,
    fast_capture_is_ready,
    format_draft_summary,
)
from flowmate.bot.handlers.notes import (
    MANAGEMENT_ALREADY_PROCESSED_MESSAGE,
    NO_NOTES_MESSAGE,
    NOTE_ALREADY_SAVED_MESSAGE,
    NOTE_EMPTY_MESSAGE,
    NOTE_LIST_FAILED_MESSAGE,
    NOTE_SAVED_MESSAGE,
    NoteSaveOutcome,
    NoteSaveStatus,
    format_note_preview,
    notes_command,
    save_note_for_message,
    selected_capture_workspace,
    text_note,
)
from flowmate.db.models import DraftSession, Note, WorkItemActionSession
from tests.ai_factories import (
    make_analysis_result,
    make_draft_item,
    make_parse_result,
    make_temporal_candidate,
)


def make_draft_result(title: str = "Prepare report") -> DraftAnalysisResult:
    return make_analysis_result(
        make_parse_result(
            [
                make_draft_item(
                    title=title,
                    description="Send it to Alex",
                    person_candidates=["Alex"],
                    topic_candidates=["Quarterly report"],
                    missing_fields=["due date"],
                    confidence=0.82,
                )
            ],
            confidence=0.82,
        )
    )


def make_draft_service(
    result: DraftAnalysisResult | SearchIntent | None = None,
    *,
    error: Exception | None = None,
) -> tuple[DraftParsingService, AsyncMock]:
    parse = AsyncMock(return_value=result or make_draft_result(), side_effect=error)
    service = MagicMock(spec=DraftParsingService)
    service.parse = parse
    service.parse_text = parse
    return cast(DraftParsingService, service), parse


def make_search_intent() -> SearchIntent:
    return SearchIntent(
        text_query=None,
        person_query="Антон",
        topic_query=None,
        item_types=[SearchWorkItemType.FOLLOW_UP],
        statuses=[],
        include_all_statuses=False,
        due_from=None,
        due_to=None,
        overdue=False,
        stale_contacts=False,
        ambiguities=[],
        confidence=0.95,
    )


def make_message(user_id: int = 123, text: str = "Заметка") -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text,
    )


def make_update(message: Message, update_id: int = 1001) -> Update:
    return Update(update_id=update_id, message=message)


def make_session() -> AsyncSession:
    session = MagicMock(spec=AsyncSession)
    scalar_result = MagicMock()
    scalar_result.one_or_none.return_value = None
    session.scalars = AsyncMock(return_value=scalar_result)
    session.scalar = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return cast(AsyncSession, session)


def make_saved_draft() -> DraftSession:
    return DraftSession(
        id=uuid4(),
        user_id=uuid4(),
        source_note_id=uuid4(),
        status="parsing",
        expires_at=datetime.now(UTC),
    )


def make_save_outcome(
    status: NoteSaveStatus,
    *,
    with_draft: bool = False,
) -> NoteSaveOutcome:
    return NoteSaveOutcome(
        status=status,
        draft=make_saved_draft() if with_draft else None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("created", "expected_result"),
    [(True, "created"), (False, "duplicate")],
)
async def test_save_note_commits_created_or_duplicate_result(
    created: bool,
    expected_result: str,
) -> None:
    message = make_message()
    update = make_update(message)
    session = make_session()
    user = SimpleNamespace(id=uuid4())
    with (
        patch(
            "flowmate.bot.handlers.notes.get_or_create_telegram_user",
            new=AsyncMock(return_value=(user, True)),
        ),
        patch(
            "flowmate.bot.handlers.notes.create_note_idempotently",
            new=AsyncMock(return_value=(object(), created)),
        ) as create_note,
    ):
        result = await save_note_for_message(
            message,
            update,
            session,
            content="  Content  ",
            source="text",
        )

    assert result.status == expected_result
    create_note.assert_awaited_once_with(
        session,
        user_id=user.id,
        content="  Content  ",
        source="text",
        telegram_update_id=1001,
    )
    cast(AsyncMock, session.commit).assert_awaited_once()
    cast(AsyncMock, session.rollback).assert_not_awaited()


@pytest.mark.asyncio
async def test_save_note_rolls_back_and_logs_no_content_on_database_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    message = make_message(text="private note contents")
    session = make_session()
    with (
        patch(
            "flowmate.bot.handlers.notes.get_or_create_telegram_user",
            new=AsyncMock(side_effect=SQLAlchemyError("private database detail")),
        ),
        caplog.at_level(logging.ERROR, logger="flowmate.bot.handlers.notes"),
    ):
        result = await save_note_for_message(
            message,
            make_update(message),
            session,
            content=message.text or "",
            source="text",
        )

    assert result.status == "failed"
    cast(AsyncMock, session.rollback).assert_awaited_once()
    assert "user_id=123" in caplog.text
    assert "private note contents" not in caplog.text
    assert "private database detail" not in caplog.text


@pytest.mark.asyncio
async def test_text_note_rejects_blank_content() -> None:
    message = make_message(text="   ")
    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await text_note(message, make_update(message), make_session())

    answer.assert_awaited_once_with(NOTE_EMPTY_MESSAGE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [("created", NOTE_SAVED_MESSAGE), ("duplicate", NOTE_ALREADY_SAVED_MESSAGE)],
)
async def test_text_note_returns_safe_save_status(
    result: NoteSaveStatus,
    expected: str,
) -> None:
    message = make_message(text="  Content  ")
    with (
        patch(
            "flowmate.bot.handlers.notes.save_note_for_message",
            new=AsyncMock(return_value=make_save_outcome(result)),
        ) as save_note,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await text_note(message, make_update(message), make_session())

    save_note.assert_awaited_once()
    answer.assert_awaited_once_with(expected)


@pytest.mark.asyncio
async def test_new_text_note_is_returned_as_compact_html_card() -> None:
    message = make_message(text="  Prepare report  ")
    service, parse = make_draft_service()
    with (
        patch(
            "flowmate.bot.handlers.notes.save_note_for_message",
            new=AsyncMock(return_value=make_save_outcome("created", with_draft=True)),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await text_note(
            message,
            make_update(message),
            make_session(),
            service,
        )

    parse.assert_awaited_once_with("Prepare report")
    assert answer.await_args_list[0].args[0] == DRAFT_ANALYZING_MESSAGE
    summary_call = answer.await_args_list[1]
    assert summary_call.args[0] == (
        "📝 <b>Проверьте запись</b>\n\n📌 <b>Задача</b>\nPrepare report"
    )
    assert summary_call.kwargs["parse_mode"] == "HTML"
    assert "reply_markup" in summary_call.kwargs
    assert len(answer.await_args_list) == 2


@pytest.mark.asyncio
async def test_record_capture_bypasses_routing_and_keeps_two_new_tasks() -> None:
    message = make_message(
        text="Добавить Личи Home Office. Отправить сообщение клиенту"
    )
    result = make_analysis_result(
        make_parse_result(
            [
                make_draft_item(title="Добавить Личи Home Office"),
                make_draft_item(title="Отправить сообщение клиенту"),
            ],
            confidence=0.95,
        )
    )
    service = MagicMock(spec=DraftParsingService)
    service.parse = AsyncMock(return_value=result)
    service.parse_text = AsyncMock()
    capture = WorkItemActionSession(
        id=uuid4(),
        user_id=uuid4(),
        action="capture_new",
        status="open",
        context={},
        expires_at=datetime(2026, 7, 28, tzinfo=UTC),
    )
    with (
        patch(
            "flowmate.bot.handlers.notes.save_note_for_message",
            new=AsyncMock(return_value=make_save_outcome("created", with_draft=True)),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await text_note(
            message,
            make_update(message),
            make_session(),
            cast(DraftParsingService, service),
            active_capture=capture,
        )

    cast(AsyncMock, service.parse).assert_awaited_once()
    cast(AsyncMock, service.parse_text).assert_not_awaited()
    response = "\n".join(str(call.args[0]) for call in answer.await_args_list)
    assert "Добавить Личи Home Office" in response
    assert "Отправить сообщение клиенту" in response
    assert "Подходящая запись не найдена" not in response


@pytest.mark.asyncio
async def test_record_capture_ai_failure_still_creates_retryable_draft() -> None:
    private_content = "private conditional task"
    message = make_message(text=private_content)
    service, parse = make_draft_service(error=AIProviderError("private model response"))
    capture = WorkItemActionSession(
        id=uuid4(),
        user_id=uuid4(),
        action="capture_new",
        status="open",
        context={},
        expires_at=datetime(2026, 7, 28, tzinfo=UTC),
    )
    save_note = AsyncMock(return_value=make_save_outcome("created", with_draft=True))
    with (
        patch(
            "flowmate.bot.handlers.notes.save_note_for_message",
            new=save_note,
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await text_note(
            message,
            make_update(message),
            make_session(),
            service,
            active_capture=capture,
        )

    assert parse.await_count == 2
    assert save_note.await_args is not None
    assert save_note.await_args.kwargs["create_draft"] is True
    assert [call.args[0] for call in answer.await_args_list] == [
        DRAFT_ANALYZING_MESSAGE,
        DRAFT_RETRY_MESSAGE,
    ]


def test_workspace_selection_uses_explicit_ai_and_current_fallback() -> None:
    analysis = make_draft_result()
    work_analysis = analysis.model_copy(
        update={"workspace_candidate": "work", "workspace_confidence": 0.95}
    )
    assert selected_capture_workspace(
        analysis,
        current="work",
        explicit="personal",
        high_confidence_threshold=0.8,
    ) == ("personal", True)
    assert selected_capture_workspace(
        work_analysis,
        current="personal",
        explicit=None,
        high_confidence_threshold=0.8,
    ) == ("work", True)
    assert selected_capture_workspace(
        analysis,
        current="personal",
        explicit=None,
        high_confidence_threshold=0.8,
    ) == ("personal", False)


def test_fast_capture_applies_default_reminder_time_and_allows_optional_fields() -> (
    None
):
    analysis = make_analysis_result(
        make_parse_result(
            [
                make_draft_item(
                    title="Перечислить деньги за Польшу",
                    missing_fields=["amount", "topic", "person"],
                    reminder_candidate=make_temporal_candidate(
                        original_phrase="7 августа",
                        normalized_value=datetime(2026, 8, 7, tzinfo=UTC),
                        time_was_explicit=False,
                    ),
                    confidence=0.95,
                )
            ],
            confidence=0.95,
        ),
        context=make_analysis_result().context.model_copy(
            update={"timezone": "Europe/Riga"}
        ),
    )

    updated = apply_default_reminder_time(analysis, default_time=time(8, 15))
    reminder = updated.items[0].item.reminder_candidate

    assert reminder is not None
    assert reminder.normalized_value is not None
    assert reminder.normalized_value.astimezone(ZoneInfo("Europe/Riga")).time() == time(
        8, 15
    )
    assert fast_capture_is_ready(updated, high_confidence_threshold=0.8)


def test_fast_capture_preserves_explicit_reminder_time() -> None:
    expected = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
    analysis = make_analysis_result(
        make_parse_result(
            [
                make_draft_item(
                    reminder_candidate=make_temporal_candidate(
                        normalized_value=expected,
                        time_was_explicit=True,
                    ),
                    confidence=0.95,
                )
            ],
            confidence=0.95,
        )
    )

    updated = apply_default_reminder_time(analysis, default_time=time(8, 15))

    assert updated.items[0].item.reminder_candidate is not None
    assert updated.items[0].item.reminder_candidate.normalized_value == expected


@pytest.mark.asyncio
async def test_duplicate_text_note_does_not_call_ai() -> None:
    message = make_message(text="Duplicate")
    service, parse = make_draft_service()
    with (
        patch(
            "flowmate.bot.handlers.notes.get_note_by_telegram_update_id",
            new=AsyncMock(return_value=Note()),
        ),
        patch(
            "flowmate.bot.handlers.notes.save_note_for_message",
            new=AsyncMock(return_value=make_save_outcome("duplicate")),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await text_note(message, make_update(message), make_session(), service)

    parse.assert_not_awaited()
    answer.assert_awaited_once_with(NOTE_ALREADY_SAVED_MESSAGE)


@pytest.mark.asyncio
async def test_duplicate_management_update_does_not_call_ai_or_save_note() -> None:
    message = make_message(text="закрой задачу")
    service, parse = make_draft_service()
    save_note = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.notes.management_update_was_processed",
            new=AsyncMock(return_value=True),
        ) as was_processed,
        patch(
            "flowmate.bot.handlers.notes.save_note_for_message",
            new=save_note,
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await text_note(message, make_update(message, 1002), make_session(), service)

    was_processed.assert_awaited_once()
    parse.assert_not_awaited()
    save_note.assert_not_awaited()
    answer.assert_awaited_once_with(MANAGEMENT_ALREADY_PROCESSED_MESSAGE)


@pytest.mark.asyncio
async def test_conversational_search_does_not_create_note_or_draft() -> None:
    message = make_message(text="Что осталось по Антону?")
    intent = make_search_intent()
    service, parse = make_draft_service(intent)
    save_note = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.notes.execute_search_intent",
            new=AsyncMock(),
        ) as execute_search,
        patch("flowmate.bot.handlers.notes.save_note_for_message", new=save_note),
    ):
        await text_note(
            message,
            make_update(message, 1003),
            make_session(),
            service,
            app_timezone=ZoneInfo("UTC"),
        )

    parse.assert_awaited_once_with("Что осталось по Антону?")
    execute_search.assert_awaited_once()
    assert execute_search.await_args is not None
    assert execute_search.await_args.args[3] == intent
    save_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_failure_keeps_saved_note_and_logs_no_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_content = "private note that must not be logged"
    message = make_message(text=private_content)
    service, parse = make_draft_service(error=AIProviderError("private model response"))
    save_note = AsyncMock(return_value=make_save_outcome("created", with_draft=True))
    with (
        patch(
            "flowmate.bot.handlers.notes.save_note_for_message",
            new=save_note,
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
        caplog.at_level(logging.WARNING, logger="flowmate.bot.handlers.notes"),
    ):
        await text_note(message, make_update(message), make_session(), service)

    save_note.assert_awaited_once()
    parse.assert_awaited_once_with(private_content)
    assert [call.args[0] for call in answer.await_args_list] == [
        NOTE_SAVED_MESSAGE,
        DRAFT_FAILED_MESSAGE,
    ]
    assert "user_id=123" in caplog.text
    assert private_content not in caplog.text
    assert "private model response" not in caplog.text


@pytest.mark.asyncio
async def test_long_draft_title_is_compact_and_within_telegram_limit() -> None:
    message = make_message(text="Long note")
    service, _ = make_draft_service(make_draft_result("x" * 8500))
    with (
        patch(
            "flowmate.bot.handlers.notes.save_note_for_message",
            new=AsyncMock(return_value=make_save_outcome("created", with_draft=True)),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await text_note(message, make_update(message), make_session(), service)

    summary_call = answer.await_args_list[1]
    assert len(summary_call.args[0]) <= 4000
    assert summary_call.kwargs["parse_mode"] == "HTML"
    assert "reply_markup" in summary_call.kwargs
    assert len(answer.await_args_list) == 2


@pytest.mark.asyncio
async def test_summary_shows_every_detected_item() -> None:
    result = make_analysis_result(
        make_parse_result(
            [
                make_draft_item(
                    type=DraftItemType.QUESTION,
                    title="Спросить лида про эскалацию",
                ),
                make_draft_item(
                    type=DraftItemType.FOLLOW_UP,
                    title="Написать Антону по срокам",
                    person_candidates=["Антон"],
                ),
                make_draft_item(
                    type=DraftItemType.NOTE,
                    title="Клиент ждёт ответ до среды",
                ),
            ]
        )
    )
    service, _ = make_draft_service(result)
    message = make_message(text="Несколько пунктов")
    with (
        patch(
            "flowmate.bot.handlers.notes.save_note_for_message",
            new=AsyncMock(return_value=make_save_outcome("created", with_draft=True)),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await text_note(message, make_update(message), make_session(), service)

    summary = answer.await_args_list[1].args[0]
    assert "1. ❓ <b>Вопрос</b>\nСпросить лида про эскалацию" in summary
    assert "2. 🔁 <b>Follow-up</b>\nНаписать Антону по срокам" in summary
    assert "3. 🗒 <b>Заметка</b>\nКлиент ждёт ответ до среды" in summary
    assert "Люди:" not in summary


def test_summary_shows_human_date_without_internal_metadata() -> None:
    due = make_temporal_candidate(
        original_phrase="до среды",
        normalized_value=datetime(2026, 7, 22, tzinfo=UTC),
        time_was_explicit=False,
    )
    dependency = DependencyCandidate(
        relation=DependencyRelation.AFTER,
        original_phrase="после этого",
        target_item_number=1,
        condition=None,
    )
    analysis = make_analysis_result(
        make_parse_result(
            [
                make_draft_item(
                    title="Confirm escalation",
                    due_date_candidate=due,
                    topic_candidates=["эскалация"],
                ),
                make_draft_item(
                    title="Write Anton",
                    dependencies=[dependency],
                ),
            ]
        )
    )

    summary = format_draft_summary(analysis)

    assert "📅 Срок: 22 июля" in summary
    assert "2026-07-22T" not in summary
    assert "Темы:" not in summary
    assert "Зависимости:" not in summary


def test_note_preview_is_safe_compact_and_bounded() -> None:
    note = Note(
        user_id=uuid4(),
        content="line one\n" + "x" * 400,
        source="voice",
        telegram_update_id=1001,
        created_at=datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
    )

    preview = format_note_preview(note, 1)

    assert preview.startswith("1. 🎙 <b>20 июля, 12:30</b>\nline one ")
    assert len(preview.split("\n", maxsplit=1)[1]) == 300
    assert preview.endswith("…")


def test_manual_note_preview_has_distinct_source_label() -> None:
    note = Note(
        user_id=uuid4(),
        content="Manual context",
        source="manual",
        telegram_update_id=None,
        created_at=datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
    )

    assert format_note_preview(note, 1).startswith("1. 🗒 <b>20 июля, 12:30</b>")


@pytest.mark.asyncio
async def test_notes_command_lists_only_requested_users_notes_as_safe_html() -> None:
    message = make_message(text="/notes")
    session = make_session()
    user = SimpleNamespace(id=uuid4())
    note = Note(
        user_id=user.id,
        content="<b>not markup</b>",
        source="text",
        telegram_update_id=1001,
        created_at=datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
    )
    with (
        patch(
            "flowmate.bot.handlers.notes.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ) as get_user,
        patch(
            "flowmate.bot.handlers.notes.list_recent_notes_for_user",
            new=AsyncMock(return_value=[note]),
        ) as list_notes,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await notes_command(message, session)

    get_user.assert_awaited_once_with(session, 123)
    list_notes.assert_awaited_once_with(session, user.id, limit=10)
    cast(AsyncMock, session.rollback).assert_awaited_once()
    answer.assert_awaited_once_with(
        "🗒 <b>Последние заметки</b>\n\n"
        "1. 🗒 <b>20 июля, 12:30</b>\n"
        "&lt;b&gt;not markup&lt;/b&gt;",
        parse_mode="HTML",
    )


@pytest.mark.asyncio
async def test_notes_command_handles_empty_list_and_database_failure() -> None:
    message = make_message(text="/notes")
    session = make_session()
    with (
        patch(
            "flowmate.bot.handlers.notes.get_user_by_telegram_id",
            new=AsyncMock(return_value=None),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await notes_command(message, session)

    answer.assert_awaited_once_with(NO_NOTES_MESSAGE)

    answer.reset_mock()
    with (
        patch(
            "flowmate.bot.handlers.notes.get_user_by_telegram_id",
            new=AsyncMock(side_effect=SQLAlchemyError("private")),
        ),
        patch.object(Message, "answer", new=answer),
    ):
        await notes_command(message, session)

    answer.assert_awaited_once_with(NOTE_LIST_FAILED_MESSAGE)
