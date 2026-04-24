import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.database import UserRepository, auth_storage
from app.keyboards import (
    MAIN_MENU_SETTINGS_CB,
    build_main_inline_menu,
    build_request_contact_keyboard,
)
from app.services import OneCService, OneCServiceError
from app.states import AuthStates
from app.utils import is_valid_phone, normalize_phone

router = Router()
logger = logging.getLogger(__name__)
one_c_service = OneCService()
user_repository = UserRepository()


def _mask_phone(phone: str) -> str:
    return f"***{phone[-4:]}" if len(phone) >= 4 else "***"


async def _is_authorized_user(user_id: int) -> bool:
    in_memory_status = auth_storage.get_user_authorization(user_id)
    if in_memory_status == "Authorized":
        return True

    user = await user_repository.get_by_user_id(user_id)
    if user is None:
        return False

    if bool(user.is_active) and bool((user.agent_id or "").strip()):
        auth_storage.set_user_authorization(user_id, "Authorized")
        return True

    return False


@router.message(CommandStart())
async def start_command_handler(message: Message, state: FSMContext) -> None:
    user = message.from_user
    user_id = user.id if user else None
    full_name = (user.full_name if user else "агент").strip()

    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити ваш Telegram ID. Спробуйте /start ще раз.")
        return

    if await _is_authorized_user(user_id):
        logger.info("Authorized user opened /start: user_id=%s", user_id)
        await state.clear()
        await message.answer(
            f"✅ <b>Вітаю, {full_name}</b>.\nОберіть дію в головному меню:",
            reply_markup=build_main_inline_menu(),
        )
        return

    logger.info("Authorization flow started: user_id=%s", user_id)
    await state.set_state(AuthStates.waiting_for_phone)
    await message.answer(
        f"<b>Вітаю, {full_name}</b>.\nДля входу надішліть номер телефону кнопкою нижче.",
        reply_markup=build_request_contact_keyboard(),
    )


@router.message(AuthStates.waiting_for_phone, F.contact)
async def receive_phone_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    contact = message.contact

    if contact is None:
        logger.warning("Contact payload missing while waiting_for_phone: user_id=%s", user_id)
        await message.answer(
            "⚠️ Не вдалося отримати контакт. Натисніть кнопку ще раз.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    if user_id is None:
        logger.warning("Cannot process contact without Telegram user id")
        await message.answer(
            "⚠️ Не вдалося визначити ваш Telegram ID. Спробуйте ще раз.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    if contact.user_id not in (None, user_id):
        logger.warning(
            "User sent foreign contact: user_id=%s contact_user_id=%s",
            user_id,
            contact.user_id,
        )
        await message.answer(
            "⚠️ Надішліть, будь ласка, свій номер телефону.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    phone_raw = (contact.phone_number or "").strip()
    phone = normalize_phone(phone_raw)
    if not phone or not is_valid_phone(phone):
        logger.warning("Invalid phone format in contact: user_id=%s phone=%s", user_id, _mask_phone(phone_raw))
        await message.answer(
            "⚠️ Номер телефону некоректний. Надішліть контакт ще раз.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    phone_digits = "".join(ch for ch in phone if ch.isdigit())
    logger.info("Phone received, requesting 1C auth: user_id=%s phone=%s", user_id, _mask_phone(phone_digits))
    try:
        auth_agent = await one_c_service.authorize_agent(phone=phone_digits, telegram_user_id=user_id)
    except OneCServiceError as exc:
        logger.exception("1C auth request failed: user_id=%s", user_id)
        if str(exc) == "Агента не знайдено в базі 1С":
            await message.answer(f"⚠️ {exc}", reply_markup=build_request_contact_keyboard())
            return
        await message.answer(
            "⚠️ Зараз не вдалося пройти авторизацію в 1С. Спробуйте ще раз трохи пізніше.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    if auth_agent is None:
        logger.warning("1C rejected auth by phone: user_id=%s", user_id)
        await message.answer(
            "⚠️ 1С не підтвердила ваш номер. Перевірте контакт або зверніться до керівника.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    full_name = (message.from_user.full_name if message.from_user else "").strip() or auth_agent.name
    try:
        await user_repository.upsert_user(
            user_id=user_id,
            phone=phone,
            full_name=full_name,
            agent_id=auth_agent.agent_id,
            agent_name=auth_agent.name,
            is_active=True,
        )
    except Exception:
        logger.exception("Failed to persist authorized user profile: user_id=%s", user_id)
        await message.answer("⚠️ Не вдалося зберегти профіль. Спробуйте ще раз або зверніться до адміністратора.")
        return

    auth_storage.set_user_authorization(user_id, "Authorized")
    await state.clear()
    logger.info(
        "Authorization success: user_id=%s agent_id=%s agent_name=%s",
        user_id,
        auth_agent.agent_id,
        auth_agent.name,
    )
    await message.answer(
        f"✅ <b>Авторизація успішна</b>.\nВітаю, {auth_agent.name}.\nОберіть дію:",
        reply_markup=build_main_inline_menu(),
    )


@router.callback_query(F.data == MAIN_MENU_SETTINGS_CB)
async def main_menu_settings_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    logger.info("Main menu settings callback: user_id=%s", user_id)

    if user_id is None:
        await callback.answer("Не вдалося визначити користувача.", show_alert=True)
        return

    user = await user_repository.get_by_user_id(user_id)
    if user is None:
        await callback.answer("Профіль не знайдено. Пройдіть /start ще раз.", show_alert=True)
        return

    phone = (user.phone or "—").strip() or "—"
    agent_id = (user.agent_id or "—").strip() or "—"
    agent_name = (user.agent_name or user.full_name or "—").strip() or "—"

    await callback.answer()
    await callback.message.answer(
        "⚙️ <b>Профіль агента</b>\n"
        f"👤 Ім'я: <b>{agent_name}</b>\n"
        f"📞 Телефон: <b>{phone}</b>\n"
        f"🆔 Agent ID: <code>{agent_id}</code>\n\n"
        "Налаштування профілю в розробці.",
        reply_markup=build_main_inline_menu(),
    )


@router.message(AuthStates.waiting_for_phone)
async def waiting_for_phone_fallback(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("Waiting for phone, non-contact input received: user_id=%s", user_id)
    await message.answer(
        "⚠️ Натисніть кнопку «Надіслати номер телефону».",
        reply_markup=build_request_contact_keyboard(),
    )


@router.message(AuthStates.waiting_for_code)
async def waiting_for_code_deprecated_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("Deprecated waiting_for_code handler called: user_id=%s", user_id)
    await state.set_state(AuthStates.waiting_for_phone)
    await message.answer(
        "⚠️ Додатковий код більше не потрібен. Надішліть свій контакт кнопкою нижче.",
        reply_markup=build_request_contact_keyboard(),
    )
