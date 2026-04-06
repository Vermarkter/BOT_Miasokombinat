import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.services import OneCService, OneCServiceError
from config import get_settings

router = Router()
logger = logging.getLogger(__name__)
one_c_service = OneCService()
settings = get_settings()


def _is_admin(message: Message) -> bool:
    if message.from_user is None:
        return False
    return message.from_user.id in settings.admin_id_set


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


@router.message(Command("check_1c"))
async def check_1c_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("check_1c command requested: user_id=%s", user_id)

    if not _is_admin(message):
        await message.answer("Команда доступна лише адміністраторам.")
        return

    try:
        is_available, status_code, status_text = await one_c_service.check_base_url_get()
    except OneCServiceError:
        logger.exception("check_1c failed due to configuration issue: user_id=%s", user_id)
        await message.answer("Сервіс 1С ще не налаштований.")
        return

    if status_code is None:
        await message.answer(f"Перевірка 1С: {status_text}")
        return

    if is_available:
        await message.answer(f"Перевірка 1С: {status_text} (HTTP {status_code})")
        return

    await message.answer(f"Перевірка 1С: {status_text} (HTTP {status_code})")
