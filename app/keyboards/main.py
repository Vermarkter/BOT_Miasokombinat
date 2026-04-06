from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.keyboards.order import SHOW_CART_BUTTON_TEXT

NEW_ORDER_BUTTON_TEXT = "Нова заявка"


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=NEW_ORDER_BUTTON_TEXT)],
            [KeyboardButton(text=SHOW_CART_BUTTON_TEXT)],
        ],
        resize_keyboard=True,
    )
