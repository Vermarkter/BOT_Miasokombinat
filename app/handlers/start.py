import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.database import UserRepository, auth_storage
from app.keyboards import build_main_keyboard, build_request_contact_keyboard
from app.services import OneCService, OneCServiceError
from app.states import AuthStates
from app.utils import is_valid_phone, normalize_phone

router = Router()
logger = logging.getLogger(__name__)
one_c_service = OneCService()
user_repository = UserRepository()


def _mask_phone(phone: str) -> str:
    return f"***{phone[-4:]}" if len(phone) >= 4 else "***"


@router.message(CommandStart())
async def start_command_handler(message: Message, state: FSMContext) -> None:
    user = message.from_user
    user_id = user.id if user else None
    full_name = (user.full_name if user else "агент").strip()

    if user_id is not None and auth_storage.get_user_authorization(user_id) == "Authorized":
        logger.info("Authorized user opened /start: user_id=%s", user_id)
        await state.clear()
        await message.answer(
            f"Вітаю, {full_name}. Ви вже авторизовані.",
            reply_markup=build_main_keyboard(),
        )
        return

    logger.info("Authorization flow started: user_id=%s", user_id)
    await state.set_state(AuthStates.waiting_for_phone)
    await message.answer(
        f"Вітаю, {full_name}. Для входу надішліть номер телефону кнопкою нижче.",
        reply_markup=build_request_contact_keyboard(),
    )


@router.message(AuthStates.waiting_for_phone, F.contact)
async def receive_phone_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    contact = message.contact

    if contact is None:
        logger.warning("Contact payload missing while waiting_for_phone: user_id=%s", user_id)
        await message.answer(
            "Не вдалося отримати контакт. Натисніть кнопку ще раз.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    if user_id is not None and contact.user_id not in (None, user_id):
        logger.warning(
            "User sent foreign contact: user_id=%s contact_user_id=%s",
            user_id,
            contact.user_id,
        )
        await message.answer(
            "Надішліть, будь ласка, свій номер телефону.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    if user_id is None:
        logger.warning("Cannot process contact without Telegram user id")
        await message.answer(
            "Не вдалося визначити ваш Telegram ID. Спробуйте ще раз.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    phone_raw = contact.phone_number.strip()
    phone = normalize_phone(phone_raw)
    if not phone or not is_valid_phone(phone):
        logger.warning("Invalid phone format in contact: user_id=%s phone=%s", user_id, _mask_phone(phone_raw))
        await message.answer(
            "Номер телефону некоректний. Надішліть контакт ще раз.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    logger.info("Phone received: user_id=%s phone=%s", user_id, _mask_phone(phone))
    try:
        is_bound = await one_c_service.bind_telegram_user(phone=phone, telegram_user_id=user_id)
    except OneCServiceError:
        logger.exception("1C telegram bind failed: user_id=%s", user_id)
        await message.answer(
            "Не вдалося перевірити контакт у 1С зараз. Спробуйте ще раз трохи пізніше.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    if not is_bound:
        logger.warning("1C rejected telegram bind: user_id=%s", user_id)
        await message.answer(
            "1С не підтвердила ваш контакт. Перевірте номер і зверніться до адміністратора.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    await state.update_data(phone=phone)
    await state.set_state(AuthStates.waiting_for_code)
    await message.answer("Контакт підтверджено в 1С. Введіть код авторизації.")


@router.message(AuthStates.waiting_for_phone)
async def waiting_for_phone_fallback(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("Waiting for phone, non-contact input received: user_id=%s", user_id)
    await message.answer(
        "Натисніть кнопку «Надіслати номер телефону».",
        reply_markup=build_request_contact_keyboard(),
    )


@router.message(AuthStates.waiting_for_code)
async def receive_code_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    code = (message.text or "").strip()

    if not code:
        logger.info("Empty authorization code received: user_id=%s", user_id)
        await message.answer("Код не може бути порожнім. Введіть код авторизації.")
        return

    state_data = await state.get_data()
    phone = normalize_phone(str(state_data.get("phone", "")).strip())
    if not phone:
        logger.warning("Phone not found in FSM data while waiting_for_code: user_id=%s", user_id)
        await state.set_state(AuthStates.waiting_for_phone)
        await message.answer(
            "Не знайдено номер телефону. Надішліть контакт ще раз.",
            reply_markup=build_request_contact_keyboard(),
        )
        return
    if not is_valid_phone(phone):
        logger.warning("Invalid phone format before auth request: user_id=%s phone=%s", user_id, _mask_phone(phone))
        await state.set_state(AuthStates.waiting_for_phone)
        await state.update_data(phone=None)
        await message.answer(
            "Схоже, номер телефону вказано некоректно. Надішліть свій номер ще раз кнопкою нижче.",
            reply_markup=build_request_contact_keyboard(),
        )
        return

    logger.info(
        "Authorization code received: user_id=%s phone=%s",
        user_id,
        _mask_phone(phone),
    )
    if user_id is None:
        logger.warning("Cannot check auth without Telegram user id")
        await message.answer("Не вдалося визначити ваш Telegram ID. Перезапустіть /start.")
        return

    try:
        is_authorized = await one_c_service.check_auth(
            phone=phone,
            code=code,
            telegram_user_id=user_id,
        )
    except OneCServiceError:
        logger.exception("Authorization check failed due to 1C error: user_id=%s", user_id)
        await message.answer("Не вдалося виконати перевірку зараз. Спробуйте ще раз трохи пізніше.")
        return

    if is_authorized:
        if user_id is not None:
            auth_storage.set_user_authorization(user_id, "Authorized")
            full_name = (message.from_user.full_name if message.from_user else "агент").strip()
            try:
                await user_repository.upsert_user(
                    user_id=user_id,
                    phone=phone,
                    full_name=full_name,
                    is_active=True,
                )
            except Exception:
                logger.exception("Failed to persist authorized user profile: user_id=%s", user_id)
        await state.clear()
        logger.info("Authorization success: user_id=%s", user_id)
        await message.answer(
            "Авторизація успішна. Доступ підтверджено.",
            reply_markup=build_main_keyboard(),
        )
        return

    logger.warning("Authorization failed: user_id=%s", user_id)
    await message.answer("Невірний код. Спробуйте ще раз.")
