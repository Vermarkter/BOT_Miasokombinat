from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.keyboards.main import build_main_keyboard

router = Router()


@router.message(CommandStart())
async def start_command_handler(message: Message) -> None:
    full_name = (message.from_user.full_name if message.from_user else "агенте").strip()
    await message.answer(
        f"Вітаю, {full_name}. Бот М'ясокомбінату готовий до роботи.",
        reply_markup=build_main_keyboard(),
    )
