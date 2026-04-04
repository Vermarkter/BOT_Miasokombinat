from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def build_request_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Надіслати номер телефону", request_contact=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
