from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Нова заявка")],
        ],
        resize_keyboard=True,
    )
