from datetime import date, timedelta

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


def get_nearest_delivery_dates(days: int = 5) -> list[str]:
    start = date.today() + timedelta(days=1)
    return [(start + timedelta(days=idx)).strftime("%Y-%m-%d") for idx in range(days)]


def build_delivery_dates_keyboard(days: int = 5) -> ReplyKeyboardMarkup:
    return build_options_keyboard(get_nearest_delivery_dates(days))
