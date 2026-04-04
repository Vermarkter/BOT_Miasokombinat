import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.services import OneCService, OneCServiceError

router = Router()
logger = logging.getLogger(__name__)
one_c_service = OneCService()


@router.message(Command("status"))
async def service_status_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("Status command requested: user_id=%s", user_id)

    try:
        is_available, status_text = await one_c_service.check_base_url_status()
    except OneCServiceError:
        logger.exception("Status check failed due to configuration issue: user_id=%s", user_id)
        await message.answer("Сервіс перевірки ще не налаштований. Зверніться до керівника або техпідтримки.")
        return

    if is_available:
        await message.answer(f"Статус 1С: {status_text}")
        return

    await message.answer(f"Статус 1С: {status_text}")
