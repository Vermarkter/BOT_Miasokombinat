import asyncio
import logging
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from app.database import CartRepository, UserRepository
from app.keyboards import (
    ADMIN_BROADCAST_BUTTON_TEXT,
    ADMIN_CANCEL_BUTTON_TEXT,
    ADMIN_STATS_BUTTON_TEXT,
    build_admin_cancel_keyboard,
    build_admin_menu_keyboard,
    build_main_keyboard,
)
from app.states import AdminStates
from config import get_settings

router = Router()
logger = logging.getLogger(__name__)
settings = get_settings()
user_repository = UserRepository()
cart_repository = CartRepository()


@dataclass(frozen=True, slots=True)
class BroadcastPayload:
    text: str | None = None
    photo_file_id: str | None = None
    caption: str | None = None


def _is_admin(message: Message) -> bool:
    if message.from_user is None:
        return False
    return message.from_user.id in settings.admin_id_set


async def _send_payload_to_user(message: Message, user_id: int, payload: BroadcastPayload) -> str:
    try:
        if payload.photo_file_id:
            await message.bot.send_photo(
                chat_id=user_id,
                photo=payload.photo_file_id,
                caption=payload.caption,
            )
        elif payload.text:
            await message.bot.send_message(chat_id=user_id, text=payload.text)
        else:
            return "failed"
        return "sent"
    except TelegramForbiddenError:
        logger.warning("Broadcast failed, user blocked bot: user_id=%s", user_id)
        try:
            await user_repository.set_is_active(user_id, False)
        except Exception:
            logger.exception("Failed to mark user inactive after block: user_id=%s", user_id)
        return "blocked"
    except TelegramRetryAfter as exc:
        logger.warning("RetryAfter while broadcasting to user_id=%s wait=%s", user_id, exc.retry_after)
        await asyncio.sleep(exc.retry_after)
        return await _send_payload_to_user(message, user_id, payload)
    except TelegramBadRequest as exc:
        logger.warning("Broadcast bad request for user_id=%s error=%s", user_id, str(exc))
        return "failed"
    except TelegramAPIError as exc:
        logger.warning("Broadcast API error for user_id=%s error=%s", user_id, str(exc))
        return "failed"
    except Exception:
        logger.exception("Unexpected broadcast error for user_id=%s", user_id)
        return "failed"


async def _broadcast(message: Message, user_ids: list[int], payload: BroadcastPayload) -> tuple[int, int, int]:
    semaphore = asyncio.Semaphore(20)

    async def worker(target_user_id: int) -> str:
        async with semaphore:
            return await _send_payload_to_user(message, target_user_id, payload)

    results = await asyncio.gather(*(worker(user_id) for user_id in user_ids))
    sent = sum(1 for item in results if item == "sent")
    blocked = sum(1 for item in results if item == "blocked")
    failed = sum(1 for item in results if item == "failed")
    return sent, blocked, failed


def _extract_broadcast_payload(message: Message) -> BroadcastPayload | None:
    text = (message.text or "").strip()
    if text and text != ADMIN_CANCEL_BUTTON_TEXT:
        return BroadcastPayload(text=text)

    if message.photo:
        largest_photo = message.photo[-1]
        caption = (message.caption or "").strip() or None
        return BroadcastPayload(photo_file_id=largest_photo.file_id, caption=caption)

    return None


@router.message(Command("admin"))
async def admin_command_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("Admin panel requested: user_id=%s", user_id)

    if not _is_admin(message):
        await message.answer("Команда доступна лише адміністраторам.")
        return

    await state.set_state(AdminStates.waiting_for_action)
    await message.answer(
        "Адмін-панель. Оберіть дію:",
        reply_markup=build_admin_menu_keyboard(),
    )


@router.message(AdminStates.waiting_for_action)
async def admin_action_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    action = (message.text or "").strip()
    logger.info("Admin action received: user_id=%s action=%s", user_id, action)

    if not _is_admin(message):
        await state.clear()
        await message.answer("Команда доступна лише адміністраторам.", reply_markup=build_main_keyboard())
        return

    if action == ADMIN_CANCEL_BUTTON_TEXT:
        await state.clear()
        await message.answer("Адмін-режим завершено.", reply_markup=build_main_keyboard())
        return

    if action == ADMIN_STATS_BUTTON_TEXT:
        users_total = await user_repository.count_users()
        users_active = await user_repository.count_active_users()
        cart_items_total = await cart_repository.count_items()
        await message.answer(
            "Статистика:\n"
            f"Користувачів у БД: {users_total}\n"
            f"Активних користувачів: {users_active}\n"
            f"Записів у CartItem: {cart_items_total}",
            reply_markup=build_admin_menu_keyboard(),
        )
        return

    if action == ADMIN_BROADCAST_BUTTON_TEXT:
        await state.set_state(AdminStates.waiting_for_broadcast_content)
        await message.answer(
            "Надішліть текст або фото з підписом для розсилки.\n"
            "Щоб скасувати, натисніть «Скасувати».",
            reply_markup=build_admin_cancel_keyboard(),
        )
        return

    await message.answer(
        "Оберіть дію з адмін-меню.",
        reply_markup=build_admin_menu_keyboard(),
    )


@router.message(AdminStates.waiting_for_broadcast_content, F.text == ADMIN_CANCEL_BUTTON_TEXT)
async def admin_broadcast_cancel_handler(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        await state.clear()
        await message.answer("Команда доступна лише адміністраторам.", reply_markup=build_main_keyboard())
        return

    await state.set_state(AdminStates.waiting_for_action)
    await message.answer("Розсилку скасовано. Оберіть наступну дію.", reply_markup=build_admin_menu_keyboard())


@router.message(AdminStates.waiting_for_broadcast_content)
async def admin_broadcast_send_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("Broadcast content received: user_id=%s", user_id)

    if not _is_admin(message):
        await state.clear()
        await message.answer("Команда доступна лише адміністраторам.", reply_markup=build_main_keyboard())
        return

    payload = _extract_broadcast_payload(message)
    if payload is None:
        await message.answer(
            "Потрібно надіслати текст або фото з підписом. Спробуйте ще раз.",
            reply_markup=build_admin_cancel_keyboard(),
        )
        return

    user_ids = await user_repository.list_active_user_ids()
    if not user_ids:
        await state.set_state(AdminStates.waiting_for_action)
        await message.answer("У таблиці users немає активних отримувачів.", reply_markup=build_admin_menu_keyboard())
        return

    await message.answer(f"Починаю розсилку для {len(user_ids)} користувачів...")
    sent, blocked, failed = await _broadcast(message, user_ids, payload)
    logger.info(
        "Broadcast finished: user_id=%s targets=%s sent=%s blocked=%s failed=%s",
        user_id,
        len(user_ids),
        sent,
        blocked,
        failed,
    )

    await state.set_state(AdminStates.waiting_for_action)
    await message.answer(
        "Розсилку завершено.\n"
        f"Доставлено: {sent}\n"
        f"Заблокували бота: {blocked}\n"
        f"Помилки доставки: {failed}",
        reply_markup=build_admin_menu_keyboard(),
    )
