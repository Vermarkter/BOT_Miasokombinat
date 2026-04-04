from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def build_options_keyboard(options: list[str], extra_buttons: list[str] | None = None) -> ReplyKeyboardMarkup:
    keyboard_rows = [[KeyboardButton(text=option)] for option in options]

    if extra_buttons:
        keyboard_rows.extend([[KeyboardButton(text=button)] for button in extra_buttons])

    return ReplyKeyboardMarkup(
        keyboard=keyboard_rows,
        resize_keyboard=True,
        one_time_keyboard=True,
    )
