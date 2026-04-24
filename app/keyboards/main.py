from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

CREATE_ORDER_BUTTON_TEXT = "📦 Створити замовлення"
PROMO_BUTTON_TEXT = "🔥 Акції"
HISTORY_BUTTON_TEXT = "🧾 Історія / Повтор замовлення"
SALES_TODAY_BUTTON_TEXT = "📊 Моя статистика за день"
PROFILE_SETTINGS_BUTTON_TEXT = "⚙️ Налаштування профілю"

MAIN_MENU_CREATE_ORDER_CB = "main:create_order"
MAIN_MENU_PROMO_CB = "main:promo"
MAIN_MENU_HISTORY_CB = "main:history"
MAIN_MENU_STATS_CB = "main:stats"
MAIN_MENU_SETTINGS_CB = "main:settings"

# Backward-compatible aliases for existing handlers/imports.
NEW_ORDER_BUTTON_TEXT = CREATE_ORDER_BUTTON_TEXT


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CREATE_ORDER_BUTTON_TEXT)],
            [KeyboardButton(text=PROMO_BUTTON_TEXT)],
            [KeyboardButton(text=HISTORY_BUTTON_TEXT)],
            [KeyboardButton(text=SALES_TODAY_BUTTON_TEXT)],
        ],
        resize_keyboard=True,
    )


def build_main_inline_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=CREATE_ORDER_BUTTON_TEXT, callback_data=MAIN_MENU_CREATE_ORDER_CB)],
            [InlineKeyboardButton(text=PROMO_BUTTON_TEXT, callback_data=MAIN_MENU_PROMO_CB)],
            [InlineKeyboardButton(text=HISTORY_BUTTON_TEXT, callback_data=MAIN_MENU_HISTORY_CB)],
            [InlineKeyboardButton(text=SALES_TODAY_BUTTON_TEXT, callback_data=MAIN_MENU_STATS_CB)],
            [InlineKeyboardButton(text=PROFILE_SETTINGS_BUTTON_TEXT, callback_data=MAIN_MENU_SETTINGS_CB)],
        ],
    )
