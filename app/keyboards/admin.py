from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

ADMIN_STATS_BUTTON_TEXT = "📊 Статистика"
ADMIN_BROADCAST_BUTTON_TEXT = "📢 Розсилка"
ADMIN_CANCEL_BUTTON_TEXT = "Скасувати"


def build_admin_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADMIN_STATS_BUTTON_TEXT)],
            [KeyboardButton(text=ADMIN_BROADCAST_BUTTON_TEXT)],
            [KeyboardButton(text=ADMIN_CANCEL_BUTTON_TEXT)],
        ],
        resize_keyboard=True,
    )


def build_admin_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADMIN_CANCEL_BUTTON_TEXT)],
        ],
        resize_keyboard=True,
    )
