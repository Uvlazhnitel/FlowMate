from importlib import import_module
from typing import Any

_EXPORTS = {
    "ManagementIntentOutcome": "management",
    "action_session_message": "sessions",
    "apply_management_intent": "management",
    "details_keyboard": "cards",
    "edit_options_keyboard": "editing",
    "encode_revision": "cards",
    "execute_management_intent": "management",
    "format_datetime": "cards",
    "format_selection_entry": "selection_presentation",
    "format_work_item_details": "cards",
    "item_action_data": "cards",
    "item_keyboard": "cards",
    "parse_user_datetime": "dates",
    "parse_work_item_callback": "cards",
    "refresh_work_item_card": "cards",
    "replied_work_item_id": "management",
    "reschedule_options_keyboard": "dates",
    "selection_keyboard": "selection_presentation",
    "send_details": "cards",
    "send_item_list": "cards",
    "snooze_options_keyboard": "reminders",
    "start_input_session": "editing",
    "work_item_callback": "callbacks",
    "work_item_callback_data": "cards",
    "work_item_selection_callback": "selection",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value
