import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.database import auth_storage
from app.keyboards import NEW_ORDER_BUTTON_TEXT, build_main_keyboard, build_options_keyboard
from app.services import OneCService
from app.states import OrderStates
from app.utils import QuantityValidationError, validate_quantity

router = Router()
logger = logging.getLogger(__name__)
one_c_service = OneCService()

FINISH_ORDER_BUTTON_TEXT = "Завершити замовлення"


def _format_quantity(value: int | float) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _format_cart_summary(cart: list[dict[str, Any]]) -> str:
    if not cart:
        return "Кошик порожній."

    lines = ["Кошик:"]
    for index, item in enumerate(cart, start=1):
        quantity = _format_quantity(item["quantity"])
        lines.append(f"{index}. {item['product']} — {quantity} {item['unit']}")
    return "\n".join(lines)


def _is_authorized(message: Message) -> bool:
    if message.from_user is None:
        return False
    return auth_storage.get_user_authorization(message.from_user.id) == "Authorized"


@router.message(Command("order"))
@router.message(F.text == NEW_ORDER_BUTTON_TEXT)
async def start_order_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("Order flow requested: user_id=%s", user_id)

    if not _is_authorized(message):
        logger.warning("Unauthorized user tried to start order flow: user_id=%s", user_id)
        await message.answer("Спочатку авторизуйтесь через /start.")
        return

    clients = one_c_service.get_clients()
    if not clients:
        logger.error("No clients available from 1C mock")
        await message.answer("Список клієнтів недоступний. Спробуйте пізніше.")
        return

    await state.set_state(OrderStates.waiting_for_client)
    await state.update_data(
        cart=[],
        available_clients=clients,
        selected_client=None,
        selected_category=None,
        selected_product=None,
        selected_unit=None,
    )
    logger.info("Order state set waiting_for_client: user_id=%s", user_id)
    await message.answer("Оберіть клієнта:", reply_markup=build_options_keyboard(clients))


@router.message(OrderStates.waiting_for_client)
async def order_client_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    selected_client = (message.text or "").strip()
    data = await state.get_data()

    clients = data.get("available_clients")
    if not isinstance(clients, list):
        clients = one_c_service.get_clients()
        await state.update_data(available_clients=clients)

    if selected_client not in clients:
        logger.info("Unknown client input: user_id=%s value=%s", user_id, selected_client)
        await message.answer(
            "Оберіть клієнта з кнопок нижче.",
            reply_markup=build_options_keyboard(clients),
        )
        return

    categories = one_c_service.get_categories()
    await state.set_state(OrderStates.waiting_for_category)
    await state.update_data(selected_client=selected_client, available_categories=categories)
    logger.info("Client selected: user_id=%s client=%s", user_id, selected_client)
    await message.answer(
        f"Клієнт: {selected_client}\nОберіть категорію товару:",
        reply_markup=build_options_keyboard(categories),
    )


@router.message(OrderStates.waiting_for_category)
async def order_category_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    selected_category = (message.text or "").strip()
    data = await state.get_data()
    cart_raw = data.get("cart", [])
    cart = list(cart_raw) if isinstance(cart_raw, list) else []

    if selected_category == FINISH_ORDER_BUTTON_TEXT:
        if not cart:
            logger.info("Finish requested with empty cart: user_id=%s", user_id)
            categories = one_c_service.get_categories()
            await message.answer(
                "Кошик порожній. Додайте хоча б один товар.",
                reply_markup=build_options_keyboard(categories),
            )
            return

        client = data.get("selected_client", "Невідомий клієнт")
        summary = _format_cart_summary(cart)
        await state.clear()
        logger.info("Order flow finished: user_id=%s cart_items=%s", user_id, len(cart))
        await message.answer(
            f"Замовлення сформовано для: {client}\n{summary}",
            reply_markup=build_main_keyboard(),
        )
        return

    categories = data.get("available_categories")
    if not isinstance(categories, list):
        categories = one_c_service.get_categories()
        await state.update_data(available_categories=categories)

    if selected_category not in categories:
        logger.info("Unknown category input: user_id=%s value=%s", user_id, selected_category)
        extra = [FINISH_ORDER_BUTTON_TEXT] if cart else None
        await message.answer(
            "Оберіть категорію з кнопок нижче.",
            reply_markup=build_options_keyboard(categories, extra),
        )
        return

    products = one_c_service.get_products(selected_category)
    product_names = [product.name for product in products]
    if not product_names:
        logger.warning("No products for category: user_id=%s category=%s", user_id, selected_category)
        extra = [FINISH_ORDER_BUTTON_TEXT] if cart else None
        await message.answer(
            "У цій категорії поки немає товарів. Оберіть іншу.",
            reply_markup=build_options_keyboard(categories, extra),
        )
        return

    await state.set_state(OrderStates.waiting_for_product)
    await state.update_data(
        selected_category=selected_category,
        available_products=product_names,
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
        available_products = [p.name for p in one_c_service.get_products(selected_category)]
        await state.update_data(available_products=available_products)

    if selected_product not in available_products:
        logger.info("Unknown product input: user_id=%s value=%s", user_id, selected_product)
        await message.answer(
            "Оберіть товар з кнопок нижче.",
            reply_markup=build_options_keyboard(available_products),
        )
        return

    product = one_c_service.find_product(selected_category, selected_product)
    if product is None:
        logger.warning(
            "Product not found in catalog after selection: user_id=%s category=%s product=%s",
            user_id,
            selected_category,
            selected_product,
        )
        await message.answer("Не вдалося знайти товар. Оберіть товар ще раз.")
        return

    await state.set_state(OrderStates.waiting_for_quantity)
    await state.update_data(selected_product=product.name, selected_unit=product.unit)
    logger.info(
        "Product selected: user_id=%s category=%s product=%s unit=%s",
        user_id,
        selected_category,
        product.name,
        product.unit,
    )
    await message.answer(f"Введіть кількість для «{product.name}» ({product.unit}).")


@router.message(OrderStates.waiting_for_quantity)
async def order_quantity_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    raw_quantity = (message.text or "").strip()
    data = await state.get_data()

    selected_client = str(data.get("selected_client", "")).strip()
    selected_category = str(data.get("selected_category", "")).strip()
    selected_product = str(data.get("selected_product", "")).strip()
    selected_unit = str(data.get("selected_unit", "")).strip()
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

    cart_item = {
        "client": selected_client,
        "category": selected_category,
        "product": selected_product,
        "quantity": quantity,
        "unit": selected_unit,
    }
    cart.append(cart_item)
    logger.info(
        "Item added to cart: user_id=%s product=%s quantity=%s unit=%s",
        user_id,
        selected_product,
        _format_quantity(quantity),
        selected_unit,
    )

    categories = one_c_service.get_categories()
    await state.set_state(OrderStates.waiting_for_category)
    await state.update_data(
        cart=cart,
        available_categories=categories,
        selected_product=None,
        selected_unit=None,
    )
    await message.answer(
        f"Товар додано в кошик.\n{_format_cart_summary(cart)}\n"
        f"Оберіть наступну категорію або натисніть «{FINISH_ORDER_BUTTON_TEXT}».",
        reply_markup=build_options_keyboard(categories, [FINISH_ORDER_BUTTON_TEXT]),
    )
