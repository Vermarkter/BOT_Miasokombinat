import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.database import auth_storage
from app.keyboards import (
    NEW_ORDER_BUTTON_TEXT,
    build_delivery_dates_keyboard,
    build_main_keyboard,
    build_options_keyboard,
    get_nearest_delivery_dates,
)
from app.services import OneCService
from app.states import OrderStates
from app.utils import QuantityValidationError, validate_quantity

router = Router()
logger = logging.getLogger(__name__)
one_c_service = OneCService()

FINISH_ORDER_BUTTON_TEXT = "Перейти до доставки"
CONFIRM_ORDER_BUTTON_TEXT = "Підтвердити замовлення"
CANCEL_ORDER_BUTTON_TEXT = "Скасувати замовлення"


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

    client_map = {client.name: client.id for client in clients}
    await state.set_state(OrderStates.waiting_for_client)
    await state.update_data(
        cart=[],
        available_clients=list(client_map.keys()),
        client_map=client_map,
        selected_client=None,
        selected_client_id=None,
        selected_category=None,
        selected_product=None,
        selected_unit=None,
        selected_price_per_unit=None,
        selected_trading_point=None,
        selected_delivery_date=None,
    )
    logger.info("Order state set waiting_for_client: user_id=%s", user_id)
    await message.answer("Оберіть клієнта:", reply_markup=build_options_keyboard(list(client_map.keys())))


@router.message(OrderStates.waiting_for_client)
async def order_client_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    selected_client = (message.text or "").strip()
    data = await state.get_data()

    clients = data.get("available_clients")
    if not isinstance(clients, list):
        clients = [client.name for client in one_c_service.get_clients()]
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
        client_map = {client.name: client.id for client in one_c_service.get_clients()}
        await state.update_data(client_map=client_map)

    selected_client_id = client_map.get(selected_client)
    if not isinstance(selected_client_id, str):
        logger.warning("Client id not found: user_id=%s client=%s", user_id, selected_client)
        await message.answer("Не вдалося визначити клієнта. Оберіть клієнта ще раз.")
        return

    categories = one_c_service.get_categories()
    await state.set_state(OrderStates.waiting_for_category)
    await state.update_data(
        selected_client=selected_client,
        selected_client_id=selected_client_id,
        available_categories=categories,
    )
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

        selected_client_id = str(data.get("selected_client_id", "")).strip()
        trading_points = one_c_service.get_trading_points(selected_client_id)
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
    await state.update_data(
        selected_product=product.name,
        selected_unit=product.unit,
        selected_price_per_unit=product.price_per_unit,
    )
    logger.info(
        "Product selected: user_id=%s category=%s product=%s unit=%s price=%.2f",
        user_id,
        selected_category,
        product.name,
        product.unit,
        product.price_per_unit,
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
    selected_price_per_unit = float(data.get("selected_price_per_unit", 0))
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
        "price_per_unit": selected_price_per_unit,
        "line_total": float(quantity) * selected_price_per_unit,
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
        selected_price_per_unit=None,
    )
    await message.answer(
        f"Товар додано в кошик.\n{_format_cart_summary(cart)}\n"
        f"Оберіть наступну категорію або натисніть «{FINISH_ORDER_BUTTON_TEXT}».",
        reply_markup=build_options_keyboard(categories, [FINISH_ORDER_BUTTON_TEXT]),
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
        awaiting_order_confirmation=False,
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
    awaiting_confirmation = bool(data.get("awaiting_order_confirmation"))

    if awaiting_confirmation:
        if user_value == CONFIRM_ORDER_BUTTON_TEXT:
            order_payload = {
                "client_id": data.get("selected_client_id"),
                "client_name": data.get("selected_client"),
                "trading_point": data.get("selected_trading_point"),
                "delivery_date": data.get("selected_delivery_date"),
                "items": data.get("cart", []),
            }
            result = one_c_service.create_order(order_payload)
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
                "Замовлення скасовано.",
                reply_markup=build_main_keyboard(),
            )
            return

        await message.answer(
            "Оберіть дію: підтвердити або скасувати замовлення.",
            reply_markup=build_options_keyboard([CONFIRM_ORDER_BUTTON_TEXT, CANCEL_ORDER_BUTTON_TEXT]),
        )
        return

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

    await state.update_data(
        selected_delivery_date=user_value,
        awaiting_order_confirmation=True,
    )
    updated_data = await state.get_data()
    summary_text = _build_order_summary(updated_data)
    logger.info("Delivery date selected, waiting confirmation: user_id=%s date=%s", user_id, user_value)
    await message.answer(
        f"{summary_text}\n\nПідтвердити відправку в 1С?",
        reply_markup=build_options_keyboard([CONFIRM_ORDER_BUTTON_TEXT, CANCEL_ORDER_BUTTON_TEXT]),
    )
