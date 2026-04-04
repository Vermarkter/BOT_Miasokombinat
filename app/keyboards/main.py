from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

NEW_ORDER_BUTTON_TEXT = "Нова заявка"


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=NEW_ORDER_BUTTON_TEXT)],
        ],
        resize_keyboard=True,
    )
