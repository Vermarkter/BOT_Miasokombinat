import asyncio
import logging
import time
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.database import CartRepository, auth_storage
from app.keyboards import (
    NEW_ORDER_BUTTON_TEXT,
    NO_COMMENT_BUTTON_TEXT,
    SHOW_CART_BUTTON_TEXT,
    build_cart_inline_keyboard,
    build_delivery_dates_keyboard,
    build_main_keyboard,
    build_options_keyboard,
    build_payment_methods_keyboard,
    build_skip_comment_keyboard,
    get_nearest_delivery_dates,
    get_payment_methods,
)
from app.services import OneCService, OneCServiceError
from app.states import OrderStates
from app.utils import QuantityValidationError, validate_quantity

router = Router()
logger = logging.getLogger(__name__)
one_c_service = OneCService()
cart_repository = CartRepository()

FINISH_ORDER_BUTTON_TEXT = "Перейти до доставки"
CONFIRM_ORDER_BUTTON_TEXT = "Підтвердити замовлення"
CANCEL_ORDER_BUTTON_TEXT = "Скасувати замовлення"
CART_DELETE_CALLBACK_PREFIX = "cart:delete:"
CART_CLEAR_CALLBACK = "cart:clear"
SUBMIT_COOLDOWN_SECONDS = 5.0
_last_submit_attempts: dict[int, float] = {}
_submit_locks: dict[int, asyncio.Lock] = {}


def _format_quantity(value: int | float) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _format_money(value: float) -> str:
    return f"{value:.2f}"


def _format_cart_summary(cart: list[dict[str, Any]]) -> str:
    if not cart:
        return "Кошик порожній."

    total_sum = 0.0
    total_weight_kg = 0.0
    lines = ["Кошик:"]
    for index, item in enumerate(cart, start=1):
        quantity = float(item.get("quantity", 0))
        unit = str(item.get("unit", ""))
        line_total = float(item.get("line_total", 0))
        total_sum += line_total
        if unit == "кг":
            total_weight_kg += quantity

        lines.append(
            f"{index}. {item.get('product', '-')}: {_format_quantity(item.get('quantity', 0))} "
            f"{unit} = {_format_money(line_total)} грн",
        )

    lines.append(f"Загальна вага: {_format_money(total_weight_kg)} кг")
    lines.append(f"Загальна сума: {_format_money(total_sum)} грн")
    return "\n".join(lines)


def _build_order_summary(order_data: dict[str, Any]) -> str:
    cart_raw = order_data.get("cart", [])
    cart = list(cart_raw) if isinstance(cart_raw, list) else []
    if not cart:
        return "Кошик порожній."

    total_sum = 0.0
    total_weight_kg = 0.0
    lines = [
        "Підтвердження замовлення:",
        f"Клієнт: {order_data.get('selected_client', '-')}",
        f"Торгова точка: {order_data.get('selected_trading_point', '-')}",
        f"Дата доставки: {order_data.get('selected_delivery_date', '-')}",
        f"Оплата: {order_data.get('selected_payment_method', '-')}",
        f"Коментар: {order_data.get('comment', 'Без коментаря')}",
        "",
        "Позиції:",
    ]

    for index, item in enumerate(cart, start=1):
        quantity = float(item.get("quantity", 0))
        unit = str(item.get("unit", ""))
        price_per_unit = float(item.get("price_per_unit", 0))
        line_total = float(item.get("line_total", quantity * price_per_unit))
        total_sum += line_total
        if unit == "кг":
            total_weight_kg += quantity

        quantity_str = _format_quantity(item.get("quantity", 0))
        lines.append(
            f"{index}. {item.get('product', '-')}: {quantity_str} {unit} x "
            f"{_format_money(price_per_unit)} грн = {_format_money(line_total)} грн",
        )

    lines.extend(
        [
            "",
            f"Загальна вага: {_format_money(total_weight_kg)} кг",
            f"Загальна сума: {_format_money(total_sum)} грн",
        ],
    )
    return "\n".join(lines)


async def _load_cart_from_db(user_id: int) -> list[dict[str, Any]]:
    db_items = await cart_repository.list_items(user_id)
    cart: list[dict[str, Any]] = []
    for item in db_items:
        quantity = _coerce_quantity(float(item.quantity), item.unit)
        cart.append(
            _build_cart_item(
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=quantity,
                unit=item.unit,
                price_per_unit=float(item.price),
            ),
        )
    return cart


async def _refresh_state_cart(state: FSMContext, user_id: int) -> list[dict[str, Any]]:
    cart = await _load_cart_from_db(user_id)
    await state.update_data(cart=cart)
    return cart


async def _send_cart_preview(message: Message, state: FSMContext, user_id: int) -> None:
    try:
        cart = await _refresh_state_cart(state, user_id)
    except Exception:
        logger.exception("Failed to load cart from database: user_id=%s", user_id)
        await message.answer("Не вдалося відкрити кошик. Спробуйте ще раз.")
        return

    if not cart:
        await message.answer("Кошик порожній. Додайте товари через /order.")
        return

    rows = _build_cart_inline_rows(cart)
    await message.answer(
        f"{_format_cart_summary(cart)}\n\nКерування кошиком:",
        reply_markup=build_cart_inline_keyboard(rows),
    )


async def _update_cart_callback_message(callback: CallbackQuery, cart: list[dict[str, Any]]) -> None:
    message = callback.message
    if message is None:
        return

    if not cart:
        await message.edit_text("Кошик порожній.")
        return

    rows = _build_cart_inline_rows(cart)
    await message.edit_text(
        f"{_format_cart_summary(cart)}\n\nКерування кошиком:",
        reply_markup=build_cart_inline_keyboard(rows),
    )


def _is_authorized(message: Message) -> bool:
    if message.from_user is None:
        return False
    return auth_storage.get_user_authorization(message.from_user.id) == "Authorized"


def _service_unavailable_message() -> str:
    return "Сервіс 1С тимчасово недоступний. Спробуйте ще раз трохи пізніше."


def _coerce_quantity(value: float, unit: str) -> int | float:
    if unit == "шт":
        return int(round(value))
    return float(value)


def _build_cart_item(
    *,
    product_id: str,
    product_name: str,
    quantity: int | float,
    unit: str,
    price_per_unit: float,
    category: str | None = None,
) -> dict[str, Any]:
    line_total = float(quantity) * price_per_unit
    return {
        "product_id": product_id,
        "category": category,
        "product": product_name,
        "quantity": quantity,
        "unit": unit,
        "price_per_unit": price_per_unit,
        "line_total": line_total,
    }


def _find_cart_item(cart: list[dict[str, Any]], product_id: str) -> dict[str, Any] | None:
    for item in cart:
        if str(item.get("product_id", "")).strip() == product_id:
            return item
    return None


def _upsert_cart_item_in_memory(
    cart: list[dict[str, Any]],
    *,
    product_id: str,
    product_name: str,
    quantity: int | float,
    unit: str,
    price_per_unit: float,
    category: str | None,
) -> list[dict[str, Any]]:
    updated_item = _build_cart_item(
        product_id=product_id,
        product_name=product_name,
        quantity=quantity,
        unit=unit,
        price_per_unit=price_per_unit,
        category=category,
    )
    for index, item in enumerate(cart):
        if str(item.get("product_id", "")).strip() == product_id:
            cart[index] = updated_item
            return cart
    cart.append(updated_item)
    return cart


def _category_extra_buttons(cart: list[dict[str, Any]]) -> list[str]:
    extra_buttons = [SHOW_CART_BUTTON_TEXT]
    if cart:
        extra_buttons.insert(0, FINISH_ORDER_BUTTON_TEXT)
    return extra_buttons


def _build_cart_inline_rows(cart: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in cart:
        product_id = str(item.get("product_id", "")).strip()
        if not product_id:
            continue

        quantity = _format_quantity(item.get("quantity", 0))
        unit = str(item.get("unit", "")).strip()
        line_total = float(item.get("line_total", 0))
        rows.append(
            {
                "product_id": product_id,
                "product_name": str(item.get("product", "-")).strip(),
                "quantity": f"{quantity} {unit}".strip(),
                "price": f"{_format_money(line_total)} грн",
            },
        )
    return rows


def _is_submit_flood(user_id: int) -> tuple[bool, int]:
    now = time.monotonic()
    last_attempt = _last_submit_attempts.get(user_id)
    if last_attempt is not None:
        elapsed = now - last_attempt
        if elapsed < SUBMIT_COOLDOWN_SECONDS:
            wait_seconds = int(SUBMIT_COOLDOWN_SECONDS - elapsed) + 1
            return True, wait_seconds

    _last_submit_attempts[user_id] = now
    return False, 0


def _get_submit_lock(user_id: int) -> asyncio.Lock:
    lock = _submit_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _submit_locks[user_id] = lock
    return lock


@router.message(Command("order"))
@router.message(F.text == NEW_ORDER_BUTTON_TEXT)
async def start_order_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("Order flow requested: user_id=%s", user_id)

    if not _is_authorized(message):
        logger.warning("Unauthorized user tried to start order flow: user_id=%s", user_id)
        await message.answer("Спочатку авторизуйтесь через /start.")
        return

    try:
        clients = await one_c_service.get_clients()
    except OneCServiceError:
        logger.exception("Failed to fetch clients: user_id=%s", user_id)
        await message.answer(_service_unavailable_message())
        return

    if not clients:
        logger.error("No clients available from 1C")
        await message.answer("Список клієнтів недоступний. Спробуйте пізніше.")
        return

    restored_cart: list[dict[str, Any]] = []
    if user_id is not None:
        try:
            restored_cart = await _load_cart_from_db(user_id)
        except Exception:
            logger.exception("Failed to restore cart from DB: user_id=%s", user_id)

    client_map = {client.name: client.id for client in clients}
    await state.set_state(OrderStates.waiting_for_client)
    await state.update_data(
        cart=restored_cart,
        available_clients=list(client_map.keys()),
        client_map=client_map,
        selected_client=None,
        selected_client_id=None,
        selected_category=None,
        selected_product=None,
        selected_product_id=None,
        selected_unit=None,
        selected_price_per_unit=None,
        selected_trading_point=None,
        selected_delivery_date=None,
        selected_payment_method=None,
        comment=None,
        awaiting_order_confirmation=False,
        order_submission_in_progress=False,
        updating_existing_item=False,
    )
    logger.info("Order state set waiting_for_client: user_id=%s", user_id)

    if restored_cart:
        await message.answer(
            "Знайдено незавершений кошик. Ви можете переглянути його кнопкою «Мій кошик».",
        )

    await message.answer("Оберіть клієнта:", reply_markup=build_options_keyboard(list(client_map.keys())))


@router.message(Command("cart"))
@router.message(F.text == SHOW_CART_BUTTON_TEXT)
async def show_cart_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("Cart preview requested: user_id=%s", user_id)

    if not _is_authorized(message):
        await message.answer("Спочатку авторизуйтесь через /start.")
        return

    if user_id is None:
        await message.answer("Не вдалося визначити користувача. Спробуйте ще раз.")
        return

    await _send_cart_preview(message, state, user_id)


@router.callback_query(F.data.startswith(CART_DELETE_CALLBACK_PREFIX))
async def cart_delete_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    callback_data = callback.data or ""
    product_id = callback_data.removeprefix(CART_DELETE_CALLBACK_PREFIX).strip()
    logger.info("Cart item delete requested: user_id=%s product_id=%s", user_id, product_id)

    if user_id is None or not product_id:
        await callback.answer("Не вдалося видалити позицію.", show_alert=True)
        return

    try:
        await cart_repository.delete_item(user_id=user_id, product_id=product_id)
        cart = await _refresh_state_cart(state, user_id)
    except Exception:
        logger.exception("Failed to delete cart item: user_id=%s product_id=%s", user_id, product_id)
        await callback.answer("Не вдалося видалити позицію. Спробуйте ще раз.", show_alert=True)
        return

    await callback.answer("Позицію видалено.")
    await _update_cart_callback_message(callback, cart)


@router.callback_query(F.data == CART_CLEAR_CALLBACK)
async def cart_clear_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    logger.info("Cart clear requested: user_id=%s", user_id)

    if user_id is None:
        await callback.answer("Не вдалося очистити кошик.", show_alert=True)
        return

    try:
        await cart_repository.clear_cart(user_id=user_id)
        cart = await _refresh_state_cart(state, user_id)
    except Exception:
        logger.exception("Failed to clear cart: user_id=%s", user_id)
        await callback.answer("Не вдалося очистити кошик. Спробуйте ще раз.", show_alert=True)
        return

    await callback.answer("Кошик очищено.")
    await _update_cart_callback_message(callback, cart)


@router.message(OrderStates.waiting_for_client)
async def order_client_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    selected_client = (message.text or "").strip()
    data = await state.get_data()

    clients = data.get("available_clients")
    if not isinstance(clients, list):
        try:
            clients = [client.name for client in await one_c_service.get_clients()]
        except OneCServiceError:
            logger.exception("Failed to refresh clients: user_id=%s", user_id)
            await message.answer(_service_unavailable_message())
            return
        await state.update_data(available_clients=clients)

    if selected_client not in clients:
        logger.info("Unknown client input: user_id=%s value=%s", user_id, selected_client)
        await message.answer(
            "Оберіть клієнта з кнопок нижче.",
            reply_markup=build_options_keyboard(clients),
        )
        return

    client_map = data.get("client_map")
    if not isinstance(client_map, dict):
        try:
            client_map = {client.name: client.id for client in await one_c_service.get_clients()}
        except OneCServiceError:
            logger.exception("Failed to refresh client map: user_id=%s", user_id)
            await message.answer(_service_unavailable_message())
            return
        await state.update_data(client_map=client_map)

    selected_client_id = client_map.get(selected_client)
    if not isinstance(selected_client_id, str):
        logger.warning("Client id not found: user_id=%s client=%s", user_id, selected_client)
        await message.answer("Не вдалося визначити клієнта. Оберіть клієнта ще раз.")
        return

    try:
        categories = await one_c_service.get_categories()
    except OneCServiceError:
        logger.exception("Failed to fetch categories: user_id=%s", user_id)
        await message.answer(_service_unavailable_message())
        return

    cart_raw = data.get("cart", [])
    cart = list(cart_raw) if isinstance(cart_raw, list) else []

    await state.set_state(OrderStates.waiting_for_category)
    await state.update_data(
        selected_client=selected_client,
        selected_client_id=selected_client_id,
        available_categories=categories,
    )
    logger.info("Client selected: user_id=%s client=%s", user_id, selected_client)
    await message.answer(
        f"Клієнт: {selected_client}\nОберіть категорію товару:",
        reply_markup=build_options_keyboard(categories, _category_extra_buttons(cart)),
    )


@router.message(OrderStates.waiting_for_category)
async def order_category_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    selected_category = (message.text or "").strip()
    data = await state.get_data()
    cart_raw = data.get("cart", [])
    cart = list(cart_raw) if isinstance(cart_raw, list) else []

    categories = data.get("available_categories")
    if not isinstance(categories, list):
        try:
            categories = await one_c_service.get_categories()
        except OneCServiceError:
            logger.exception("Failed to refresh categories: user_id=%s", user_id)
            await message.answer(_service_unavailable_message())
            return
        await state.update_data(available_categories=categories)

    if selected_category == SHOW_CART_BUTTON_TEXT:
        if user_id is not None:
            await _send_cart_preview(message, state, user_id)
        else:
            await message.answer("Не вдалося визначити користувача. Спробуйте ще раз.")

        refreshed_data = await state.get_data()
        refreshed_cart_raw = refreshed_data.get("cart", [])
        refreshed_cart = list(refreshed_cart_raw) if isinstance(refreshed_cart_raw, list) else []
        await message.answer(
            "Оберіть категорію товару:",
            reply_markup=build_options_keyboard(categories, _category_extra_buttons(refreshed_cart)),
        )
        return

    if selected_category == FINISH_ORDER_BUTTON_TEXT:
        if not cart:
            logger.info("Finish requested with empty cart: user_id=%s", user_id)
            await message.answer(
                "Кошик порожній. Додайте хоча б один товар.",
                reply_markup=build_options_keyboard(categories, _category_extra_buttons(cart)),
            )
            return

        selected_client_id = str(data.get("selected_client_id", "")).strip()
        try:
            trading_points = await one_c_service.get_trading_points(selected_client_id)
        except OneCServiceError:
            logger.exception("Failed to fetch trading points: user_id=%s client_id=%s", user_id, selected_client_id)
            await message.answer(_service_unavailable_message())
            return

        if not trading_points:
            logger.warning("No trading points for client: user_id=%s client_id=%s", user_id, selected_client_id)
            await message.answer("Для цього клієнта не знайдено торгових точок.")
            return

        await state.set_state(OrderStates.waiting_for_trading_point)
        await state.update_data(available_trading_points=trading_points)
        logger.info(
            "Switching to trading point selection: user_id=%s client_id=%s",
            user_id,
            selected_client_id,
        )
        await message.answer(
            "Оберіть торгову точку:",
            reply_markup=build_options_keyboard(trading_points),
        )
        return

    if selected_category not in categories:
        logger.info("Unknown category input: user_id=%s value=%s", user_id, selected_category)
        await message.answer(
            "Оберіть категорію з кнопок нижче.",
            reply_markup=build_options_keyboard(categories, _category_extra_buttons(cart)),
        )
        return

    try:
        products = await one_c_service.get_products(selected_category)
    except OneCServiceError:
        logger.exception("Failed to fetch products: user_id=%s category=%s", user_id, selected_category)
        await message.answer(_service_unavailable_message())
        return

    product_names = [product.name for product in products]
    if not product_names:
        logger.warning("No products for category: user_id=%s category=%s", user_id, selected_category)
        await message.answer(
            "У цій категорії поки немає товарів. Оберіть іншу.",
            reply_markup=build_options_keyboard(categories, _category_extra_buttons(cart)),
        )
        return

    products_map = {
        product.name: {
            "product_id": product.id,
            "unit": product.unit,
            "price_per_unit": product.price_per_unit,
        }
        for product in products
    }
    await state.set_state(OrderStates.waiting_for_product)
    await state.update_data(
        selected_category=selected_category,
        available_products=product_names,
        products_map=products_map,
    )
    logger.info("Category selected: user_id=%s category=%s", user_id, selected_category)
    await message.answer(
        "Оберіть товар:",
        reply_markup=build_options_keyboard(product_names),
    )


@router.message(OrderStates.waiting_for_product)
async def order_product_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    selected_product = (message.text or "").strip()
    data = await state.get_data()
    selected_category = str(data.get("selected_category", "")).strip()

    available_products = data.get("available_products")
    if not isinstance(available_products, list):
        try:
            available_products = [p.name for p in await one_c_service.get_products(selected_category)]
        except OneCServiceError:
            logger.exception("Failed to refresh products: user_id=%s category=%s", user_id, selected_category)
            await message.answer(_service_unavailable_message())
            return
        await state.update_data(available_products=available_products)

    if selected_product not in available_products:
        logger.info("Unknown product input: user_id=%s value=%s", user_id, selected_product)
        await message.answer(
            "Оберіть товар з кнопок нижче.",
            reply_markup=build_options_keyboard(available_products),
        )
        return

    products_map = data.get("products_map")
    if not isinstance(products_map, dict):
        products_map = {}

    product_data = products_map.get(selected_product)
    if not isinstance(product_data, dict):
        try:
            product = await one_c_service.find_product(selected_category, selected_product)
        except OneCServiceError:
            logger.exception(
                "Failed to find product in 1C: user_id=%s category=%s product=%s",
                user_id,
                selected_category,
                selected_product,
            )
            await message.answer(_service_unavailable_message())
            return
        if product is None:
            logger.warning(
                "Product not found in catalog after selection: user_id=%s category=%s product=%s",
                user_id,
                selected_category,
                selected_product,
            )
            await message.answer("Не вдалося знайти товар. Оберіть товар ще раз.")
            return
        product_data = {
            "product_id": product.id,
            "unit": product.unit,
            "price_per_unit": product.price_per_unit,
        }

    selected_product_id = str(product_data.get("product_id", selected_product)).strip() or selected_product
    selected_unit = str(product_data.get("unit", "")).strip()
    selected_price_per_unit = float(product_data.get("price_per_unit", 0))

    cart_raw = data.get("cart", [])
    cart = list(cart_raw) if isinstance(cart_raw, list) else []
    existing_item = _find_cart_item(cart, selected_product_id)

    await state.set_state(OrderStates.waiting_for_quantity)
    await state.update_data(
        selected_product=selected_product,
        selected_product_id=selected_product_id,
        selected_unit=selected_unit,
        selected_price_per_unit=selected_price_per_unit,
        updating_existing_item=existing_item is not None,
    )
    logger.info(
        "Product selected: user_id=%s category=%s product=%s product_id=%s unit=%s price=%.2f",
        user_id,
        selected_category,
        selected_product,
        selected_product_id,
        selected_unit,
        selected_price_per_unit,
    )

    if existing_item is not None:
        await message.answer(
            f"Товар «{selected_product}» вже є в кошику: "
            f"{_format_quantity(existing_item.get('quantity', 0))} {selected_unit}.\n"
            "Введіть нову кількість, щоб оновити цю позицію.",
        )
        return

    await message.answer(f"Введіть кількість для «{selected_product}» ({selected_unit}).")


@router.message(OrderStates.waiting_for_quantity)
async def order_quantity_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    raw_quantity = (message.text or "").strip()
    data = await state.get_data()

    selected_category = str(data.get("selected_category", "")).strip()
    selected_product = str(data.get("selected_product", "")).strip()
    selected_product_id = str(data.get("selected_product_id", selected_product)).strip() or selected_product
    selected_unit = str(data.get("selected_unit", "")).strip()
    selected_price_per_unit = float(data.get("selected_price_per_unit", 0))
    is_update = bool(data.get("updating_existing_item"))
    cart_raw = data.get("cart", [])
    cart = list(cart_raw) if isinstance(cart_raw, list) else []

    try:
        quantity = validate_quantity(raw_quantity, selected_unit)
    except QuantityValidationError as exc:
        logger.info(
            "Quantity validation failed: user_id=%s product=%s unit=%s raw=%s error=%s",
            user_id,
            selected_product,
            selected_unit,
            raw_quantity,
            str(exc),
        )
        await message.answer(str(exc))
        return

    if user_id is not None:
        try:
            await cart_repository.upsert_item(
                user_id=user_id,
                product_id=selected_product_id,
                product_name=selected_product,
                quantity=float(quantity),
                price=selected_price_per_unit,
                unit=selected_unit,
            )
            cart = await _load_cart_from_db(user_id)
        except Exception:
            logger.exception(
                "Failed to upsert cart item in DB: user_id=%s product_id=%s",
                user_id,
                selected_product_id,
            )
            await message.answer("Не вдалося зберегти товар у кошик. Спробуйте ще раз.")
            return
    else:
        cart = _upsert_cart_item_in_memory(
            cart,
            product_id=selected_product_id,
            product_name=selected_product,
            quantity=quantity,
            unit=selected_unit,
            price_per_unit=selected_price_per_unit,
            category=selected_category,
        )

    logger.info(
        "Cart item upserted: user_id=%s product=%s product_id=%s quantity=%s unit=%s",
        user_id,
        selected_product,
        selected_product_id,
        _format_quantity(quantity),
        selected_unit,
    )

    try:
        categories = await one_c_service.get_categories()
    except OneCServiceError:
        logger.exception("Failed to fetch categories after item upsert: user_id=%s", user_id)
        await message.answer(_service_unavailable_message())
        return

    await state.set_state(OrderStates.waiting_for_category)
    await state.update_data(
        cart=cart,
        available_categories=categories,
        selected_product=None,
        selected_product_id=None,
        selected_unit=None,
        selected_price_per_unit=None,
        products_map={},
        updating_existing_item=False,
    )

    action_text = "Кількість оновлено в кошику." if is_update else "Товар додано в кошик."
    await message.answer(
        f"{action_text}\n{_format_cart_summary(cart)}\n"
        f"Оберіть наступну категорію, «{SHOW_CART_BUTTON_TEXT}» або «{FINISH_ORDER_BUTTON_TEXT}».",
        reply_markup=build_options_keyboard(categories, _category_extra_buttons(cart)),
    )


@router.message(OrderStates.waiting_for_trading_point)
async def order_trading_point_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    selected_trading_point = (message.text or "").strip()
    data = await state.get_data()
    trading_points = data.get("available_trading_points")
    if not isinstance(trading_points, list):
        trading_points = []

    if selected_trading_point not in trading_points:
        logger.info("Unknown trading point input: user_id=%s value=%s", user_id, selected_trading_point)
        await message.answer(
            "Оберіть торгову точку з кнопок нижче.",
            reply_markup=build_options_keyboard(trading_points),
        )
        return

    delivery_dates = get_nearest_delivery_dates()
    await state.set_state(OrderStates.waiting_for_delivery_date)
    await state.update_data(
        selected_trading_point=selected_trading_point,
        available_delivery_dates=delivery_dates,
        selected_delivery_date=None,
        selected_payment_method=None,
        comment=None,
        awaiting_order_confirmation=False,
        order_submission_in_progress=False,
    )
    logger.info("Trading point selected: user_id=%s point=%s", user_id, selected_trading_point)
    await message.answer(
        "Оберіть дату доставки:",
        reply_markup=build_delivery_dates_keyboard(),
    )


@router.message(OrderStates.waiting_for_delivery_date)
async def order_delivery_date_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    user_value = (message.text or "").strip()
    data = await state.get_data()

    delivery_dates = data.get("available_delivery_dates")
    if not isinstance(delivery_dates, list):
        delivery_dates = get_nearest_delivery_dates()
        await state.update_data(available_delivery_dates=delivery_dates)

    if user_value not in delivery_dates:
        logger.info("Unknown delivery date input: user_id=%s value=%s", user_id, user_value)
        await message.answer(
            "Оберіть дату доставки з кнопок нижче.",
            reply_markup=build_delivery_dates_keyboard(),
        )
        return

    await state.set_state(OrderStates.waiting_for_payment_method)
    await state.update_data(
        selected_delivery_date=user_value,
        available_payment_methods=get_payment_methods(),
        selected_payment_method=None,
    )
    logger.info("Delivery date selected: user_id=%s date=%s", user_id, user_value)
    await message.answer(
        "Оберіть спосіб оплати:",
        reply_markup=build_payment_methods_keyboard(),
    )


@router.message(OrderStates.waiting_for_payment_method)
async def order_payment_method_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    selected_payment_method = (message.text or "").strip()
    data = await state.get_data()

    payment_methods = data.get("available_payment_methods")
    if not isinstance(payment_methods, list):
        payment_methods = get_payment_methods()
        await state.update_data(available_payment_methods=payment_methods)

    if selected_payment_method not in payment_methods:
        logger.info(
            "Unknown payment method input: user_id=%s value=%s",
            user_id,
            selected_payment_method,
        )
        await message.answer(
            "Оберіть спосіб оплати з кнопок нижче.",
            reply_markup=build_payment_methods_keyboard(),
        )
        return

    await state.set_state(OrderStates.waiting_for_comment)
    await state.update_data(
        selected_payment_method=selected_payment_method,
        comment=None,
        awaiting_order_confirmation=False,
        order_submission_in_progress=False,
    )
    logger.info("Payment method selected: user_id=%s method=%s", user_id, selected_payment_method)
    await message.answer(
        "Введіть коментар до замовлення або натисніть «Без коментаря».",
        reply_markup=build_skip_comment_keyboard(),
    )


@router.message(OrderStates.waiting_for_comment)
async def order_comment_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    user_value = (message.text or "").strip()
    data = await state.get_data()

    awaiting_confirmation = bool(data.get("awaiting_order_confirmation"))
    if awaiting_confirmation:
        if user_value == CONFIRM_ORDER_BUTTON_TEXT:
            if user_id is None:
                await message.answer("Не вдалося визначити користувача. Спробуйте ще раз.")
                return

            submit_lock = _get_submit_lock(user_id)
            if submit_lock.locked():
                await message.answer("Замовлення вже відправляється. Зачекайте, будь ласка.")
                return

            async with submit_lock:
                fresh_data = await state.get_data()
                if not bool(fresh_data.get("awaiting_order_confirmation")):
                    await message.answer("Замовлення вже оброблено.")
                    return

                if bool(fresh_data.get("order_submission_in_progress")):
                    await message.answer("Замовлення вже відправляється. Зачекайте, будь ласка.")
                    return

                is_flood, wait_seconds = _is_submit_flood(user_id)
                if is_flood:
                    await message.answer(
                        f"Запит уже обробляється. Зачекайте {wait_seconds} сек. і повторіть підтвердження.",
                    )
                    return

                await state.update_data(order_submission_in_progress=True)
                order_payload = {
                    "client_id": fresh_data.get("selected_client_id"),
                    "client_name": fresh_data.get("selected_client"),
                    "trading_point": fresh_data.get("selected_trading_point"),
                    "delivery_date": fresh_data.get("selected_delivery_date"),
                    "payment_method": fresh_data.get("selected_payment_method"),
                    "comment": fresh_data.get("comment"),
                    "items": fresh_data.get("cart", []),
                }
                try:
                    result = await one_c_service.create_order(order_payload)
                except OneCServiceError:
                    _last_submit_attempts.pop(user_id, None)
                    await state.update_data(order_submission_in_progress=False)
                    logger.exception("Order submit failed: user_id=%s", user_id)
                    await message.answer(_service_unavailable_message())
                    return

                try:
                    await cart_repository.clear_cart(user_id)
                except Exception:
                    logger.exception("Failed to clear cart after order submit: user_id=%s", user_id)

                order_number = str(result.get("order_number", "N/A"))
                await state.clear()
                logger.info("Order submitted successfully: user_id=%s order_number=%s", user_id, order_number)
                await message.answer(
                    f"Замовлення успішно відправлено в 1С.\nНомер замовлення: {order_number}",
                    reply_markup=build_main_keyboard(),
                )
                return

        if user_value == CANCEL_ORDER_BUTTON_TEXT:
            await state.clear()
            logger.info("Order submission cancelled by user: user_id=%s", user_id)
            await message.answer(
                "Замовлення скасовано. Кошик збережено, можете повернутись до нього пізніше.",
                reply_markup=build_main_keyboard(),
            )
            return

        await message.answer(
            "Оберіть дію: підтвердити або скасувати замовлення.",
            reply_markup=build_options_keyboard([CONFIRM_ORDER_BUTTON_TEXT, CANCEL_ORDER_BUTTON_TEXT]),
        )
        return

    if not user_value:
        await message.answer(
            "Коментар не може бути порожнім. Введіть коментар або натисніть «Без коментаря».",
            reply_markup=build_skip_comment_keyboard(),
        )
        return

    comment: str | None
    if user_value == NO_COMMENT_BUTTON_TEXT:
        comment = None
    else:
        comment = user_value

    await state.update_data(
        comment=comment,
        awaiting_order_confirmation=True,
        order_submission_in_progress=False,
    )
    updated_data = await state.get_data()
    summary_text = _build_order_summary(updated_data)
    logger.info("Comment processed, waiting confirmation: user_id=%s", user_id)
    await message.answer(
        f"{summary_text}\n\nПідтвердити відправку в 1С?",
        reply_markup=build_options_keyboard([CONFIRM_ORDER_BUTTON_TEXT, CANCEL_ORDER_BUTTON_TEXT]),
    )
