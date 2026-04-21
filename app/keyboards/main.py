from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

CREATE_ORDER_BUTTON_TEXT = "📦 Створити замовлення"
HISTORY_BUTTON_TEXT = "📂 Історія замовлень"
SALES_TODAY_BUTTON_TEXT = "💰 Мої продажі за сьогодні"

# Backward-compatible alias for existing handlers/imports.
NEW_ORDER_BUTTON_TEXT = CREATE_ORDER_BUTTON_TEXT


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CREATE_ORDER_BUTTON_TEXT)],
            [KeyboardButton(text=HISTORY_BUTTON_TEXT)],
            [KeyboardButton(text=SALES_TODAY_BUTTON_TEXT)],
        ],
        resize_keyboard=True,
    )
