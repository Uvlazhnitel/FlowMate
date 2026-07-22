# ruff: noqa: RUF001
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User, Voice
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.errors import AIProviderError
from flowmate.ai.schemas import DraftSource, TemporalCandidate, TemporalStatus
from flowmate.ai.service import DraftParsingService
from flowmate.bot.filters import ActiveDraftFilter
from flowmate.bot.handlers.clarification import active_draft_message
from flowmate.bot.handlers.drafts import (
    DRAFT_CONVERSION_FAILED_MESSAGE,
    DRAFT_EXPIRED_MESSAGE,
    DRAFT_FAILED_MESSAGE,
    DRAFT_NOT_FOUND_MESSAGE,
    DRAFT_REPLY_REQUIRED_MESSAGE,
    analyze_note_content,
    draft_callback,
    refine_draft,
)
from flowmate.db.models import DraftSession
from flowmate.drafts.questions import next_clarification_question
from flowmate.speech.service import TranscriptionService
from flowmate.task_engine.conversion import (
    DraftConversionResult,
    DraftConversionService,
    conversion_summary,
)
from tests.ai_factories import (
    make_analysis_result,
    make_context,
    make_draft_item,
    make_parse_result,
)


def make_session() -> AsyncSession:
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    return cast(AsyncSession, session)


def make_message(
    *,
    message_id: int = 20,
    text: str | None = None,
    voice: Voice | None = None,
    reply_to: Message | None = None,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=123, type="private"),
        from_user=User(id=123, is_bot=False, first_name="Test"),
        text=text,
        voice=voice,
        reply_to_message=reply_to,
    )


def make_draft(
    *,
    status: str = "needs_clarification",
    expires_at: datetime | None = None,
) -> DraftSession:
    analysis = make_analysis_result(
        make_parse_result(
            [make_draft_item(missing_fields=["due date"], confidence=0.7)]
        )
    )
    return DraftSession(
        id=uuid4(),
        user_id=uuid4(),
        source_note_id=uuid4(),
        status=status,
        analysis_payload=analysis.model_dump(mode="json"),
        current_question="Когда выполнить задачу?",
        current_question_options=[],
        current_question_message_id=10,
        processed_update_ids=[],
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )


def make_service(result: object | None = None) -> DraftParsingService:
    service = MagicMock(spec=DraftParsingService)
    service.refine = AsyncMock(
        return_value=result
        or make_analysis_result(make_parse_result([make_draft_item()]))
    )
    return cast(DraftParsingService, service)


def make_conversion_service(draft: DraftSession) -> DraftConversionService:
    service = MagicMock(spec=DraftConversionService)
    result = DraftConversionResult(
        draft_id=draft.id,
        work_items=(),
        notes=(),
        counts={},
    )

    async def convert(*_: object, **__: object) -> DraftConversionResult:
        draft.status = "confirmed"
        return result

    service.convert = AsyncMock(side_effect=convert)
    return cast(DraftConversionService, service)


def make_callback(draft_id: UUID, action: str) -> tuple[CallbackQuery, Update]:
    message = make_message(text="Draft controls")
    callback = CallbackQuery(
        id="callback-id",
        from_user=User(id=123, is_bot=False, first_name="Test"),
        chat_instance="chat",
        message=message,
        data=f"draft:{action}:{draft_id}",
    )
    return callback, Update(update_id=500, callback_query=callback)


def test_question_planner_asks_one_critical_question() -> None:
    analysis = make_analysis_result(
        make_parse_result(
            [
                make_draft_item(missing_fields=["due date", "person"]),
                make_draft_item(missing_fields=["topic"]),
            ]
        )
    )

    question = next_clarification_question(analysis)

    assert question is not None
    assert "due date" in question.text
    assert question.context == {"item_number": 1, "field": "missing_field"}


@pytest.mark.asyncio
async def test_active_draft_filter_intercepts_ordinary_text() -> None:
    message = make_message(text="обычная новая заметка")
    draft = make_draft()
    user_id = draft.user_id
    with (
        patch(
            "flowmate.bot.filters.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=user_id)),
        ),
        patch(
            "flowmate.bot.filters.get_active_draft_for_user",
            new=AsyncMock(return_value=draft),
        ),
    ):
        result = await ActiveDraftFilter()(
            message,
            make_session(),
            Update(update_id=499, message=message),
        )

    assert isinstance(result, dict)
    assert result == {"active_draft": draft, "draft_user_id": user_id}


@pytest.mark.asyncio
async def test_refinement_sends_existing_draft_context_to_provider() -> None:
    provider = MagicMock()
    provider.parse = AsyncMock(return_value=make_parse_result([make_draft_item()]))
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("UTC"),
        active_workspace="personal",
        timeout_seconds=2,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=lambda _: datetime(2026, 7, 21, 12, tzinfo=UTC),
    )
    current = make_analysis_result(
        make_parse_result([make_draft_item(title="Original")])
    )

    await service.refine(
        current,
        "не Антон, а Мария",
        answer_source=DraftSource.TEXT,
        question="Кто отвечает?",
    )

    call = provider.parse.await_args
    assert call.kwargs["user_text"] == "не Антон, а Мария"
    assert "Original" in call.kwargs["system_prompt"]
    assert "Кто отвечает?" in call.kwargs["system_prompt"]
    assert "complete updated draft" in call.kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_exact_date_refinement_bypasses_ai_and_replaces_month_end() -> None:
    provider = MagicMock()
    provider.parse = AsyncMock()
    service = DraftParsingService(
        provider,
        timezone=ZoneInfo("Europe/Riga"),
        active_workspace="personal",
        timeout_seconds=2,
        high_confidence_threshold=0.8,
        clarification_confidence_threshold=0.5,
        clock=lambda _: datetime(2026, 7, 22, 12, tzinfo=UTC),
    )
    month_end = TemporalCandidate(
        original_phrase="в августе",
        normalized_value=datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC),
        status=TemporalStatus.RESOLVED,
        explanation="Конец августа",
        time_was_explicit=False,
    )
    reminder = TemporalCandidate(
        original_phrase="в августе",
        normalized_value=None,
        status=TemporalStatus.AMBIGUOUS,
        explanation="Нужно уточнить дату напоминания",
        time_was_explicit=False,
    )
    current = make_analysis_result(
        make_parse_result(
            [
                make_draft_item(
                    due_date_candidate=month_end,
                    reminder_candidate=reminder,
                    ambiguities=["Неясно, когда напомнить"],
                )
            ],
            ambiguities=["Нужно уточнить дату"],
        ),
        context=make_context(
            timezone="Europe/Riga",
            current_datetime=datetime(2026, 7, 22, 12, tzinfo=UTC),
        ),
    )

    result = await service.refine(
        current,
        "7 августа",
        answer_source=DraftSource.TEXT,
        question="Уточните напоминание.",
    )

    provider.parse.assert_not_awaited()
    due = result.items[0].item.due_date_candidate
    assert due is not None
    assert due.normalized_value == datetime(
        2026, 8, 7, 23, 59, 59, tzinfo=ZoneInfo("Europe/Riga")
    )
    assert result.items[0].item.reminder_candidate is None
    assert result.ambiguities == []


@pytest.mark.asyncio
async def test_unthreaded_message_does_not_update_active_draft() -> None:
    message = make_message(text="не в пятницу, а в понедельник")
    service = make_service()
    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await active_draft_message(
            message,
            Update(update_id=501, message=message),
            make_session(),
            cast(Bot, MagicMock()),
            uuid4(),
            active_draft=make_draft(),
            draft_parsing_service=service,
        )

    answer.assert_awaited_once_with(DRAFT_REPLY_REQUIRED_MESSAGE)
    cast(AsyncMock, service.refine).assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_clarification_update_does_not_call_ai() -> None:
    question = make_message(message_id=10, text="Question")
    message = make_message(text="в понедельник", reply_to=question)
    service = make_service()
    with (
        patch(
            "flowmate.bot.handlers.drafts.claim_update",
            new=AsyncMock(return_value=("duplicate", make_draft())),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock),
    ):
        await active_draft_message(
            message,
            Update(update_id=502, message=message),
            make_session(),
            cast(Bot, MagicMock()),
            uuid4(),
            active_draft=make_draft(),
            draft_parsing_service=service,
        )

    cast(AsyncMock, service.refine).assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_ai_failure_stays_safe_when_failed_transition_fails() -> None:
    message = make_message(text="private draft contents")
    draft = make_draft(status="parsing")
    service = make_service()
    cast(AsyncMock, service.refine).reset_mock()
    cast(Any, service).parse = AsyncMock(
        side_effect=AIProviderError("private provider response")
    )
    session = make_session()
    with (
        patch(
            "flowmate.bot.handlers.drafts.transition_draft",
            new=AsyncMock(side_effect=SQLAlchemyError("private database detail")),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await analyze_note_content(
            message,
            content=message.text or "",
            telegram_user_id=123,
            source=DraftSource.TEXT,
            service=service,
            db_session=session,
            draft=draft,
            draft_ttl_hours=24,
        )

    cast(AsyncMock, session.rollback).assert_awaited_once()
    answer.assert_awaited_once_with(DRAFT_FAILED_MESSAGE)


@pytest.mark.asyncio
async def test_refinement_database_failure_releases_claimed_update() -> None:
    message = make_message(text="не Антон, а Мария")
    draft = make_draft()
    session = make_session()
    service = make_service()
    clear_processing = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.drafts.claim_update",
            new=AsyncMock(return_value=("claimed", draft)),
        ),
        patch(
            "flowmate.bot.handlers.drafts.replace_draft_analysis",
            new=AsyncMock(side_effect=SQLAlchemyError("private database detail")),
        ),
        patch(
            "flowmate.bot.handlers.drafts.clear_processing_update",
            new=clear_processing,
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await refine_draft(
            message,
            draft=draft,
            answer=message.text or "",
            answer_source=DraftSource.TEXT,
            update_id=505,
            user_id=draft.user_id,
            telegram_user_id=123,
            service=service,
            db_session=session,
            draft_ttl_hours=24,
        )

    clear_processing.assert_awaited_once_with(session, draft)
    assert cast(AsyncMock, session.rollback).await_count == 1
    assert cast(AsyncMock, session.commit).await_count == 2
    answer.assert_awaited_once_with(DRAFT_FAILED_MESSAGE)


@pytest.mark.asyncio
async def test_voice_answer_refines_existing_draft() -> None:
    question = make_message(message_id=10, text="Question")
    message = make_message(
        voice=Voice(
            file_id="voice",
            file_unique_id="voice-unique",
            duration=1,
            file_size=5,
        ),
        reply_to=question,
    )
    draft = make_draft()
    transcription = MagicMock(spec=TranscriptionService)
    transcription.is_too_large.return_value = False
    transcription.transcribe = AsyncMock(return_value="это заметка")
    service = make_service()
    session = make_session()
    with (
        patch(
            "flowmate.bot.handlers.clarification.claim_update",
            new=AsyncMock(return_value=("claimed", draft)),
        ),
        patch(
            "flowmate.bot.handlers.drafts.replace_draft_analysis",
            new=AsyncMock(),
        ),
        patch(
            "flowmate.bot.handlers.drafts.show_draft",
            new=AsyncMock(),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock),
    ):
        await active_draft_message(
            message,
            Update(update_id=503, message=message),
            session,
            cast(Bot, MagicMock()),
            draft.user_id,
            active_draft=draft,
            transcription_service=cast(TranscriptionService, transcription),
            draft_parsing_service=service,
        )

    cast(AsyncMock, transcription.transcribe).assert_awaited_once()
    cast(AsyncMock, service.refine).assert_awaited_once()
    assert draft.source_note_id is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "status", "response"),
    [
        (
            "сохрани как есть",
            "confirmed",
            conversion_summary(
                DraftConversionResult(
                    draft_id=UUID(int=0),
                    work_items=(),
                    notes=(),
                    counts={},
                )
            ),
        ),
        (
            "отмена",
            "cancelled",
            "Черновик отменён. Исходная заметка сохранена.",
        ),
    ],
)
async def test_control_phrase_closes_same_draft_idempotently(
    phrase: str,
    status: str,
    response: str,
) -> None:
    draft = make_draft()
    message = make_message(text=phrase)
    conversion_service = make_conversion_service(draft)
    with (
        patch(
            "flowmate.bot.handlers.clarification.claim_update",
            new=AsyncMock(return_value=("claimed", draft)),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await active_draft_message(
            message,
            Update(update_id=504, message=message),
            make_session(),
            cast(Bot, MagicMock()),
            draft.user_id,
            active_draft=draft,
            draft_conversion_service=conversion_service,
        )

    assert draft.status == status
    assert draft.processed_update_ids == []
    answer.assert_awaited_once_with(response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_status", "expected_message"),
    [
        (
            "confirm",
            "confirmed",
            conversion_summary(
                DraftConversionResult(
                    draft_id=UUID(int=0),
                    work_items=(),
                    notes=(),
                    counts={},
                )
            ),
        ),
        ("cancel", "cancelled", "Черновик отменён. Исходная заметка сохранена."),
    ],
)
async def test_draft_callbacks_transition_owned_session(
    action: str,
    expected_status: str,
    expected_message: str,
) -> None:
    draft = make_draft(status="ready")
    callback, update = make_callback(draft.id, action)
    session = make_session()
    conversion_service = make_conversion_service(draft)
    with (
        patch(
            "flowmate.bot.handlers.drafts.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=draft.user_id)),
        ),
        patch(
            "flowmate.bot.handlers.drafts.get_draft_for_user",
            new=AsyncMock(return_value=draft),
        ),
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        patch.object(Message, "edit_text", new_callable=AsyncMock) as edit_text,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await draft_callback(
            callback,
            update,
            session,
            draft_conversion_service=conversion_service,
        )

    assert draft.status == expected_status
    edit_text.assert_awaited_once_with(expected_message)
    answer.assert_awaited_once()
    menu_call = answer.await_args
    assert menu_call is not None
    assert menu_call.args == ("Можно записать следующий пункт.",)
    assert menu_call.kwargs["parse_mode"] is None
    assert menu_call.kwargs["reply_markup"].is_persistent is True


@pytest.mark.asyncio
async def test_expired_and_foreign_drafts_reject_callbacks() -> None:
    expired = make_draft(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    callback, update = make_callback(expired.id, "confirm")
    session = make_session()
    with (
        patch(
            "flowmate.bot.handlers.drafts.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=expired.user_id)),
        ),
        patch(
            "flowmate.bot.handlers.drafts.get_draft_for_user",
            new=AsyncMock(return_value=expired),
        ),
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
    ):
        await draft_callback(callback, update, session)
    answer.assert_awaited_once_with(DRAFT_EXPIRED_MESSAGE, show_alert=True)
    assert expired.status == "expired"

    callback, update = make_callback(expired.id, "confirm")
    with (
        patch(
            "flowmate.bot.handlers.drafts.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ),
        patch(
            "flowmate.bot.handlers.drafts.get_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
    ):
        await draft_callback(callback, update, session)
    answer.assert_awaited_once_with(DRAFT_NOT_FOUND_MESSAGE, show_alert=True)


@pytest.mark.asyncio
async def test_callback_database_failure_returns_safe_error() -> None:
    draft = make_draft()
    callback, update = make_callback(draft.id, "confirm")
    session = make_session()
    with (
        patch(
            "flowmate.bot.handlers.drafts.get_user_by_telegram_id",
            new=AsyncMock(side_effect=SQLAlchemyError("private detail")),
        ),
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
    ):
        await draft_callback(callback, update, session)

    cast(AsyncMock, session.rollback).assert_awaited_once()
    answer.assert_awaited_once_with(
        DRAFT_CONVERSION_FAILED_MESSAGE,
        show_alert=True,
    )
