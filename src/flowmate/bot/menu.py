import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

logger = logging.getLogger(__name__)

RECORD_BUTTON = "🎙 Записать"
TODAY_BUTTON = "📅 Сегодня"
TASKS_BUTTON = "✅ Задачи"
FOLLOW_UPS_BUTTON = "🔁 Follow-up"
WAITING_BUTTON = "⏳ Ждём"
QUESTIONS_BUTTON = "❓ Вопросы"
PEOPLE_BUTTON = "👥 Люди"
TOPICS_BUTTON = "🗂 Темы"
SEARCH_BUTTON = "🔍 Поиск"
SETTINGS_BUTTON = "⚙️ Настройки"
WORKSPACE_BUTTON = "🔀 Работа / Личное"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=RECORD_BUTTON), KeyboardButton(text=TODAY_BUTTON)],
            [KeyboardButton(text=TASKS_BUTTON), KeyboardButton(text=FOLLOW_UPS_BUTTON)],
            [
                KeyboardButton(text=WAITING_BUTTON),
                KeyboardButton(text=QUESTIONS_BUTTON),
            ],
            [KeyboardButton(text=PEOPLE_BUTTON), KeyboardButton(text=TOPICS_BUTTON)],
            [KeyboardButton(text=SEARCH_BUTTON), KeyboardButton(text=SETTINGS_BUTTON)],
            [KeyboardButton(text=WORKSPACE_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Отправьте текст или голосовое сообщение",
    )


async def answer_with_main_menu(message: Message, text: str) -> bool:
    try:
        await message.answer(
            text,
            parse_mode=None,
            reply_markup=main_menu_keyboard(),
        )
    except TelegramAPIError:
        logger.warning("telegram_main_menu_send_failed category=telegram")
        return False
    return True


async def restore_main_menu(
    message: Message, text: str = "Можно записать следующий пункт."
) -> bool:
    return await answer_with_main_menu(message, text)
