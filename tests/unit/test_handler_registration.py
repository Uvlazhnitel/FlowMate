from unittest.mock import MagicMock

from flowmate.bot.handlers.commands import create_router


def test_handler_packages_import_without_cycles() -> None:
    from flowmate.bot.handlers.navigation.search import complete_search_action
    from flowmate.bot.handlers.work_items.cards import send_details
    from flowmate.bot.handlers.work_items.sessions import action_session_message

    assert complete_search_action.__module__.endswith("navigation.search")
    assert send_details.__module__.endswith("work_items.cards")
    assert action_session_message.__module__.endswith("work_items.sessions")


def test_router_registration_order_is_stable() -> None:
    router = create_router(frozenset(), MagicMock(), MagicMock())

    assert [handler.callback.__name__ for handler in router.message.handlers] == [
        "start_command",
        "menu_command",
        "help_command",
        "status_command",
        "workspace_command",
        "notes_command",
        "draft_command",
        "cancel_command",
        "cancel_command",
        "today_command",
        "tomorrow_command",
        "tasks_command",
        "followups_command",
        "waiting_command",
        "questions_command",
        "topics_command",
        "people_command",
        "reminders_settings_command",
        "quiet_command",
        "snooze_command",
        "search_command",
        "record_prompt",
        "today_command",
        "tomorrow_command",
        "tasks_command",
        "followups_command",
        "waiting_command",
        "questions_command",
        "search_command",
        "reminders_settings_command",
        "workspace_toggle",
        "voice_message",
        "text_note",
        "action_session_message",
        "active_draft_message",
        "voice_message",
        "text_note",
        "unsupported_message",
    ]
    assert [
        handler.callback.__name__ for handler in router.callback_query.handlers
    ] == [
        "workspace_callback",
        "draft_callback",
        "reminder_callback",
        "digest_callback",
        "list_callback",
        "search_callback",
        "menu_callback",
        "work_item_callback",
        "work_item_selection_callback",
    ]
