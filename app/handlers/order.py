import asyncio
import logging
import time
from datetime import date
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.database import CartRepository, UserRepository, auth_storage
from app.keyboards import (
    HISTORY_BUTTON_TEXT,
    MAIN_MENU_CREATE_ORDER_CB,
    MAIN_MENU_HISTORY_CB,
    MAIN_MENU_PROMO_CB,
    MAIN_MENU_STATS_CB,
    NEW_ORDER_BUTTON_TEXT,
    NO_COMMENT_BUTTON_TEXT,
    PROMO_BUTTON_TEXT,
    SALES_TODAY_BUTTON_TEXT,
    SHOW_CART_BUTTON_TEXT,
    build_cart_inline_keyboard,
    build_main_inline_menu,
    build_options_keyboard,
    build_skip_comment_keyboard,
)
from app.services import OneCService, OneCServiceError
from app.states import OrderStates
from app.utils import QuantityValidationError, validate_quantity
from config import settings

router = Router()
logger = logging.getLogger(__name__)
one_c_service = OneCService()
cart_repository = CartRepository()
user_repository = UserRepository()

FINISH_ORDER_BUTTON_TEXT = "✅ Оформити замовлення"
LAST_ORDERS_BUTTON_TEXT = "🧾 Останні замовлення клієнта"
BACK_BUTTON_TEXT = "⬅️ Назад"
CONFIRM_ORDER_BUTTON_TEXT = "✅ Підтвердити"
CANCEL_ORDER_BUTTON_TEXT = "❌ Скасувати"

CART_DELETE_CALLBACK_PREFIX = "cart:delete:"
CART_DECREASE_CALLBACK_PREFIX = "cart:minus:"
CART_INCREASE_CALLBACK_PREFIX = "cart:plus:"
CART_NOOP_CALLBACK = "cart:noop"
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


def _format_debt_money(value: float) -> str:
    return f"{value:,.2f}"


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, str):
            value = value.replace(",", ".")
        return float(value)
    except (TypeError, ValueError):
        return default


def _service_unavailable_message(error: OneCServiceError | None = None) -> str:
    if error is not None and str(error) == "Агента не знайдено в базі 1С":
        return "⚠️ Агента не знайдено в базі 1С"
    return "⚠️ Сервіс 1С тимчасово недоступний. Ви можете продовжити в демо-режимі, якщо він увімкнений."


def _cart_step(unit: str) -> float:
    # One-handed quick control: +/- by 1 for any unit (шт / кг).
    _ = unit
    return 1.0


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
) -> dict[str, Any]:
    line_total = float(quantity) * price_per_unit
    return {
        "product_id": product_id,
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


def _format_cart_summary(cart: list[dict[str, Any]]) -> str:
    if not cart:
        return "🧺 <b>Кошик порожній.</b>"

    total_sum = 0.0
    total_weight_kg = 0.0
    lines = ["🧺 <b>Кошик</b>:"]
    for index, item in enumerate(cart, start=1):
        quantity = float(item.get("quantity", 0))
        unit = str(item.get("unit", ""))
        line_total = float(item.get("line_total", 0))
        total_sum += line_total
        if unit == "кг":
            total_weight_kg += quantity

        lines.append(
            f"{index}. <b>{item.get('product', '-')}</b> — "
            f"{_format_quantity(item.get('quantity', 0))} {unit} — "
            f"<i>{_format_money(line_total)} грн</i>",
        )

    lines.append(f"⚖️ Вага: <b>{_format_money(total_weight_kg)} кг</b>")
    lines.append(f"💵 Сума: <b>{_format_money(total_sum)} грн</b>")
    return "\n".join(lines)


def _build_order_summary(order_data: dict[str, Any]) -> str:
    cart_raw = order_data.get("cart", [])
    cart = list(cart_raw) if isinstance(cart_raw, list) else []
    if not cart:
        return "🧺 <b>Кошик порожній.</b>"

    total_sum = 0.0
    total_weight_kg = 0.0
    lines = [
        "🚚 <b>Підтвердження замовлення</b>:",
        f"Клієнт: <b>{order_data.get('selected_client', '-')}</b>",
        f"Договір: <b>{order_data.get('selected_contract', '-')}</b>",
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

        lines.append(
            f"{index}. <b>{item.get('product', '-')}</b>: {_format_quantity(item.get('quantity', 0))} {unit} x "
            f"<i>{_format_money(price_per_unit)} грн</i> = <i>{_format_money(line_total)} грн</i>",
        )

    lines.append("")
    lines.append(f"⚖️ Вага: <b>{_format_money(total_weight_kg)} кг</b>")
    lines.append(f"💵 Сума: <b>{_format_money(total_sum)} грн</b>")
    return "\n".join(lines)


def _build_create_order_payload(order_data: dict[str, Any]) -> dict[str, Any]:
    cart_raw = order_data.get("cart", [])
    cart = list(cart_raw) if isinstance(cart_raw, list) else []

    products: list[dict[str, Any]] = []
    for raw_item in cart:
        if not isinstance(raw_item, dict):
            continue
        product_id = str(raw_item.get("product_id", "")).strip()
        if not product_id:
            continue
        quantity = _coerce_quantity(_to_float(raw_item.get("quantity", 0), default=0.0), str(raw_item.get("unit", "")))
        price = _to_float(
            raw_item.get("price_per_unit", raw_item.get("price", raw_item.get("unit_price", 0))),
            default=0.0,
        )
        products.append(
            {
                "id": product_id,
                "quantity": quantity,
                "price": price,
            },
        )

    return {
        "client_id": str(order_data.get("selected_client_id", "")).strip(),
        "contract_id": str(order_data.get("selected_contract_id", "")).strip(),
        "products": products,
    }


def _build_history_message(history_rows: list[dict[str, Any]], client_name: str) -> str:
    if not history_rows:
        return f"🧾 Для клієнта <b>{client_name}</b> поки немає замовлень."

    lines = [f"🧾 <b>Останні замовлення</b> клієнта <b>{client_name}</b>:"]
    for index, row in enumerate(history_rows[:10], start=1):
        number = str(row.get("order_number", "-")).strip() or "-"
        row_date = str(row.get("date", "-")).strip() or "-"
        total = _to_float(row.get("total", 0.0), default=0.0)
        lines.append(f"{index}. №<b>{number}</b> | {row_date} | <i>{_format_money(total)} грн</i>")
    return "\n".join(lines)


def _calc_sales_today(history_rows: list[dict[str, Any]]) -> tuple[int, float]:
    today_iso = date.today().isoformat()
    count = 0
    total = 0.0
    for row in history_rows:
        order_date = str(row.get("date", "")).strip()
        if not order_date.startswith(today_iso):
            continue
        count += 1
        total += _to_float(row.get("total", 0.0), default=0.0)
    return count, total


def _build_labeled_map(
    rows: list[dict[str, Any]],
    *,
    folder_key: str | None = None,
    folder_prefix: str = "📁 ",
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    labels: list[str] = []
    labeled_map: dict[str, dict[str, Any]] = {}

    for row in rows:
        name = str(row.get("name", "")).strip()
        if not name:
            continue

        base_label = name
        if folder_key and bool(row.get(folder_key)):
            base_label = f"{folder_prefix}{name}"

        final_label = base_label
        duplicate_index = 2
        while final_label in labeled_map:
            final_label = f"{base_label} ({duplicate_index})"
            duplicate_index += 1

        labels.append(final_label)
        labeled_map[final_label] = row

    return labels, labeled_map


def _serialize_clients(rows: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": str(row.id),
                "name": str(row.name),
                "is_folder": bool(row.is_folder),
            },
        )
    return result


def _serialize_contracts(rows: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": str(row.id),
                "name": str(row.name),
                "price_type_id": str(row.price_type_id),
            },
        )
    return result


def _serialize_products(rows: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        is_promotional = bool(getattr(row, "is_promotional", False))
        product_name = str(row.name)
        if is_promotional and not product_name.startswith("🔥 "):
            product_name = f"🔥 {product_name}"
        result.append(
            {
                "id": str(row.id),
                "name": product_name,
                "is_folder": bool(row.is_folder),
                "unit": str(row.unit),
                "price": float(row.price),
                "is_promotional": is_promotional,
            },
        )
    return result


def _filter_products_for_promo(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if bool(row.get("is_folder")) or bool(row.get("is_promotional")):
            filtered.append(row)
    return filtered


def _serialize_history_rows(rows: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "order_number": str(row.order_number),
                "date": str(row.date),
                "total": float(row.total),
            },
        )
    return result


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


def _build_contract_prompt(
    client_name: str,
    client_debt: float | None,
    *,
    suffix: str = "Будь ласка, оберіть договір для замовлення:",
) -> str:
    lines = [f"Клієнт: <b>{client_name}</b>"]
    if client_debt is None:
        lines.append("💰 Поточний борг: <b>немає даних</b>")
    else:
        lines.append(f"💰 Поточний борг: <b>{_format_debt_money(client_debt)} грн</b>")
    if suffix.strip():
        lines.append(suffix)
    return "\n".join(lines)


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


async def _resolve_agent_id(user_id: int, state: FSMContext) -> str | None:
    data = await state.get_data()
    state_agent_id = str(data.get("agent_id", "")).strip()
    if state_agent_id:
        return state_agent_id

    user = await user_repository.get_by_user_id(user_id)
    if user is None:
        return None

    agent_id = str(user.agent_id or "").strip()
    if not agent_id:
        return None

    await state.update_data(agent_id=agent_id)
    return agent_id


async def _resolve_first_client_for_history(user_id: int, state: FSMContext) -> tuple[str, str] | None:
    data = await state.get_data()
    selected_client_id = str(data.get("selected_client_id", "")).strip()
    selected_client_name = str(data.get("selected_client", "")).strip()
    if selected_client_id:
        return selected_client_id, selected_client_name or "Клієнт"

    agent_id = await _resolve_agent_id(user_id, state)
    if not agent_id:
        return None

    clients = await _fetch_clients_for_parent(user_id=user_id, agent_id=agent_id, parent_id=None)
    for row in clients:
        if not bool(row.get("is_folder")):
            return str(row.get("id", "")).strip(), str(row.get("name", "Клієнт")).strip() or "Клієнт"

    for row in clients:
        if not bool(row.get("is_folder")):
            continue
        nested = await _fetch_clients_for_parent(
            user_id=user_id,
            agent_id=agent_id,
            parent_id=str(row.get("id", "")).strip() or None,
        )
        for nested_row in nested:
            if not bool(nested_row.get("is_folder")):
                return (
                    str(nested_row.get("id", "")).strip(),
                    str(nested_row.get("name", "Клієнт")).strip() or "Клієнт",
                )

    return None


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
        await message.answer("⚠️ Не вдалося відкрити кошик. Спробуйте ще раз.")
        return

    if not cart:
        await message.answer("🧺 <b>Кошик порожній.</b>\nДодайте товари через /order.")
        return

    rows = _build_cart_inline_rows(cart)
    await message.answer(
        f"{_format_cart_summary(cart)}\n\nНатискайте <b>➖ / ➕</b> для зміни кількості:",
        reply_markup=build_cart_inline_keyboard(rows),
    )


async def _update_cart_callback_message(callback: CallbackQuery, cart: list[dict[str, Any]]) -> None:
    message = callback.message
    if message is None:
        return

    if not cart:
        await message.edit_text("🧺 <b>Кошик порожній.</b>")
        return

    rows = _build_cart_inline_rows(cart)
    await message.edit_text(
        f"{_format_cart_summary(cart)}\n\nНатискайте <b>➖ / ➕</b> для зміни кількості:",
        reply_markup=build_cart_inline_keyboard(rows),
    )


def _client_extra_buttons(data: dict[str, Any]) -> list[str]:
    buttons: list[str] = []
    history = data.get("client_parent_history")
    if isinstance(history, list) and history:
        buttons.append(BACK_BUTTON_TEXT)
    buttons.append(SHOW_CART_BUTTON_TEXT)
    return buttons


def _contract_extra_buttons() -> list[str]:
    return [LAST_ORDERS_BUTTON_TEXT, BACK_BUTTON_TEXT, SHOW_CART_BUTTON_TEXT]


def _product_extra_buttons(cart: list[dict[str, Any]]) -> list[str]:
    buttons = [BACK_BUTTON_TEXT, SHOW_CART_BUTTON_TEXT]
    if cart:
        buttons.insert(0, FINISH_ORDER_BUTTON_TEXT)
    return buttons


async def _send_client_menu(message: Message, state: FSMContext, *, text: str | None = None) -> None:
    data = await state.get_data()
    labels = data.get("client_labels")
    if not isinstance(labels, list):
        labels = []
    prompt = text or "<b>Оберіть клієнта або папку</b>:"
    await message.answer(
        prompt,
        reply_markup=build_options_keyboard(labels, _client_extra_buttons(data)),
    )


async def _send_contract_menu(message: Message, state: FSMContext, *, text: str | None = None) -> None:
    data = await state.get_data()
    labels = data.get("contract_labels")
    if not isinstance(labels, list):
        labels = []
    selected_client = str(data.get("selected_client", "-")).strip() or "-"
    client_debt_raw = data.get("selected_client_debt")
    client_debt = _to_float(client_debt_raw, default=0.0) if client_debt_raw is not None else None
    prompt = text or _build_contract_prompt(selected_client, client_debt)
    await message.answer(
        prompt,
        reply_markup=build_options_keyboard(labels, _contract_extra_buttons()),
    )


async def _send_product_menu(message: Message, state: FSMContext, *, text: str | None = None) -> None:
    data = await state.get_data()
    labels = data.get("product_labels")
    if not isinstance(labels, list):
        labels = []
    cart_raw = data.get("cart", [])
    cart = list(cart_raw) if isinstance(cart_raw, list) else []
    promo_only = bool(data.get("promo_only"))
    prompt = text or ("<b>Оберіть папку або акційний товар</b>:" if promo_only else "<b>Оберіть папку або товар</b>:")
    await message.answer(
        prompt,
        reply_markup=build_options_keyboard(labels, _product_extra_buttons(cart)),
    )


async def _fetch_clients_for_parent(
    *,
    user_id: int,
    agent_id: str,
    parent_id: str | None,
) -> list[dict[str, Any]]:
    clients = await one_c_service.get_clients(
        agent_id=agent_id,
        parent_id=parent_id,
        telegram_user_id=user_id,
    )
    return _serialize_clients(clients)


async def _set_client_scope(
    state: FSMContext,
    *,
    parent_id: str | None,
    parent_history: list[str | None],
    rows: list[dict[str, Any]],
) -> None:
    labels, label_map = _build_labeled_map(rows, folder_key="is_folder")
    await state.update_data(
        client_parent_id=parent_id,
        client_parent_history=parent_history,
        current_clients=rows,
        client_labels=labels,
        client_label_map=label_map,
    )


async def _set_contract_scope(state: FSMContext, rows: list[dict[str, Any]]) -> None:
    labels, label_map = _build_labeled_map(rows)
    await state.update_data(
        current_contracts=rows,
        contract_labels=labels,
        contract_label_map=label_map,
    )


async def _fetch_and_set_product_scope(
    state: FSMContext,
    *,
    user_id: int,
    price_type_id: str,
    parent_id: str | None,
    parent_history: list[str | None],
    promo_only: bool,
) -> list[dict[str, Any]]:
    products = await one_c_service.get_products(
        price_type_id=price_type_id,
        parent_id=parent_id,
        telegram_user_id=user_id,
    )
    serialized = _serialize_products(products)
    if promo_only:
        serialized = _filter_products_for_promo(serialized)
    labels, label_map = _build_labeled_map(serialized, folder_key="is_folder")
    await state.update_data(
        product_parent_id=parent_id,
        product_parent_history=parent_history,
        current_products=serialized,
        product_labels=labels,
        product_label_map=label_map,
        promo_only=promo_only,
    )
    return serialized


async def _start_order_flow(message: Message, state: FSMContext, *, user_id: int, promo_only: bool = False) -> None:
    logger.info("Order flow requested: user_id=%s", user_id)

    if not await _is_authorized_user(user_id):
        logger.warning("Unauthorized user tried to start order flow: user_id=%s", user_id)
        await message.answer("⚠️ Спочатку авторизуйтеся через /start.")
        return

    agent_id = await _resolve_agent_id(user_id, state)
    if not agent_id:
        await message.answer("⚠️ Не знайдено дані агента. Пройдіть авторизацію через /start ще раз.")
        return

    try:
        current_clients = await _fetch_clients_for_parent(user_id=user_id, agent_id=agent_id, parent_id=None)
    except OneCServiceError as exc:
        logger.exception("Failed to fetch clients: user_id=%s", user_id)
        await message.answer(_service_unavailable_message(exc))
        return

    if not current_clients:
        await message.answer("⚠️ Список клієнтів порожній. Спробуйте пізніше.")
        return

    restored_cart: list[dict[str, Any]] = []
    try:
        restored_cart = await _load_cart_from_db(user_id)
    except Exception:
        logger.exception("Failed to restore cart from DB: user_id=%s", user_id)

    await state.set_state(OrderStates.waiting_for_client)
    await state.update_data(
        agent_id=agent_id,
        cart=restored_cart,
        promo_only=promo_only,
        selected_client=None,
        selected_client_id=None,
        selected_client_debt=None,
        selected_contract=None,
        selected_contract_id=None,
        selected_price_type_id=None,
        selected_product=None,
        selected_product_id=None,
        selected_unit=None,
        selected_price_per_unit=None,
        awaiting_order_confirmation=False,
        order_submission_in_progress=False,
        comment=None,
        updating_existing_item=False,
        product_parent_id=None,
        product_parent_history=[],
    )
    await _set_client_scope(
        state,
        parent_id=None,
        parent_history=[],
        rows=current_clients,
    )

    if restored_cart:
        await message.answer("🧺 Знайдено незавершений кошик. За потреби відкрийте його кнопкою «Мій кошик».")
    await _send_client_menu(
        message,
        state,
        text="🔥 Оберіть клієнта або папку для акційного замовлення:" if promo_only else None,
    )


async def _show_history(message: Message, state: FSMContext, *, user_id: int) -> None:
    if not await _is_authorized_user(user_id):
        await message.answer("⚠️ Спочатку авторизуйтеся через /start.")
        return

    try:
        client_info = await _resolve_first_client_for_history(user_id, state)
    except OneCServiceError as exc:
        logger.exception("Failed to resolve client for history: user_id=%s", user_id)
        await message.answer(_service_unavailable_message(exc))
        return

    if client_info is None:
        await message.answer("⚠️ Не знайдено клієнтів. Почніть з /order.")
        return

    client_id, client_name = client_info
    try:
        history = await one_c_service.get_orders_history(
            client_id=client_id,
            telegram_user_id=user_id,
        )
    except OneCServiceError as exc:
        logger.exception("Failed to fetch order history: user_id=%s client_id=%s", user_id, client_id)
        await message.answer(_service_unavailable_message(exc))
        return

    history_rows = _serialize_history_rows(history)
    await message.answer(_build_history_message(history_rows, client_name), reply_markup=build_main_inline_menu())


async def _show_sales_today(message: Message, state: FSMContext, *, user_id: int) -> None:
    if not await _is_authorized_user(user_id):
        await message.answer("⚠️ Спочатку авторизуйтеся через /start.")
        return

    try:
        client_info = await _resolve_first_client_for_history(user_id, state)
    except OneCServiceError as exc:
        logger.exception("Failed to resolve client for sales report: user_id=%s", user_id)
        await message.answer(_service_unavailable_message(exc))
        return

    if client_info is None:
        await message.answer("⚠️ Не знайдено клієнтів для звіту. Почніть з /order.")
        return

    client_id, client_name = client_info
    try:
        history = await one_c_service.get_orders_history(
            client_id=client_id,
            telegram_user_id=user_id,
        )
    except OneCServiceError as exc:
        logger.exception("Failed to fetch orders for sales report: user_id=%s client_id=%s", user_id, client_id)
        await message.answer(_service_unavailable_message(exc))
        return

    history_rows = _serialize_history_rows(history)
    count, total = _calc_sales_today(history_rows)
    await message.answer(
        "📊 <b>Моя статистика за день</b>\n"
        f"Клієнт: <b>{client_name}</b>\n"
        f"Замовлень: <b>{count}</b>\n"
        f"Сума: <i>{_format_money(total)} грн</i>",
        reply_markup=build_main_inline_menu(),
    )


@router.message(Command("order"))
@router.message(F.text == NEW_ORDER_BUTTON_TEXT)
async def start_order_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити користувача. Спробуйте ще раз.")
        return
    await _start_order_flow(message, state, user_id=user_id)


@router.message(F.text == PROMO_BUTTON_TEXT)
async def start_promo_order_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити користувача. Спробуйте ще раз.")
        return
    await _start_order_flow(message, state, user_id=user_id, promo_only=True)


@router.callback_query(F.data == MAIN_MENU_CREATE_ORDER_CB)
async def start_order_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None:
        await callback.answer("Не вдалося визначити користувача.", show_alert=True)
        return
    await callback.answer()
    if callback.message is None:
        return
    await _start_order_flow(callback.message, state, user_id=user_id)


@router.callback_query(F.data == MAIN_MENU_PROMO_CB)
async def start_promo_order_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None:
        await callback.answer("Не вдалося визначити користувача.", show_alert=True)
        return
    await callback.answer()
    if callback.message is None:
        return
    await _start_order_flow(callback.message, state, user_id=user_id, promo_only=True)


@router.message(Command("history"))
@router.message(F.text == HISTORY_BUTTON_TEXT)
async def history_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити користувача. Спробуйте ще раз.")
        return
    await _show_history(message, state, user_id=user_id)


@router.callback_query(F.data == MAIN_MENU_HISTORY_CB)
async def history_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None:
        await callback.answer("Не вдалося визначити користувача.", show_alert=True)
        return
    await callback.answer()
    if callback.message is None:
        return
    await _show_history(callback.message, state, user_id=user_id)


@router.message(F.text == SALES_TODAY_BUTTON_TEXT)
async def sales_today_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити користувача. Спробуйте ще раз.")
        return
    await _show_sales_today(message, state, user_id=user_id)


@router.callback_query(F.data == MAIN_MENU_STATS_CB)
async def sales_today_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None:
        await callback.answer("Не вдалося визначити користувача.", show_alert=True)
        return
    await callback.answer()
    if callback.message is None:
        return
    await _show_sales_today(callback.message, state, user_id=user_id)


@router.message(Command("support"))
async def support_handler(message: Message) -> None:
    await message.answer(
        "📞 <b>Зв'язок з офісом</b>\n"
        f"{settings.support_contact}\n"
        "Якщо є проблема із заявкою, напишіть номер точки та короткий опис.",
    )


@router.message(Command("cart"))
@router.message(F.text == SHOW_CART_BUTTON_TEXT)
async def show_cart_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити користувача. Спробуйте ще раз.")
        return
    if not await _is_authorized_user(user_id):
        await message.answer("⚠️ Спочатку авторизуйтеся через /start.")
        return
    await _send_cart_preview(message, state, user_id)


@router.callback_query(F.data == CART_NOOP_CALLBACK)
async def cart_noop_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith(CART_DELETE_CALLBACK_PREFIX))
async def cart_delete_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    callback_data = callback.data or ""
    product_id = callback_data.removeprefix(CART_DELETE_CALLBACK_PREFIX).strip()

    if user_id is None or not product_id:
        await callback.answer("⚠️ Не вдалося видалити позицію.", show_alert=True)
        return

    try:
        await cart_repository.delete_item(user_id=user_id, product_id=product_id)
        cart = await _refresh_state_cart(state, user_id)
    except Exception:
        logger.exception("Failed to delete cart item: user_id=%s product_id=%s", user_id, product_id)
        await callback.answer("⚠️ Не вдалося видалити позицію.", show_alert=True)
        return

    await callback.answer("✅ Позицію видалено.")
    await _update_cart_callback_message(callback, cart)


@router.callback_query(F.data.startswith(CART_DECREASE_CALLBACK_PREFIX))
async def cart_decrease_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await _change_cart_quantity(callback, state, delta=-1.0)


@router.callback_query(F.data.startswith(CART_INCREASE_CALLBACK_PREFIX))
async def cart_increase_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await _change_cart_quantity(callback, state, delta=1.0)


async def _change_cart_quantity(callback: CallbackQuery, state: FSMContext, *, delta: float) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    callback_data = callback.data or ""
    prefix = CART_INCREASE_CALLBACK_PREFIX if delta > 0 else CART_DECREASE_CALLBACK_PREFIX
    product_id = callback_data.removeprefix(prefix).strip()

    if user_id is None or not product_id:
        await callback.answer("⚠️ Не вдалося змінити кількість.", show_alert=True)
        return

    try:
        current_item = await cart_repository.get_item(user_id=user_id, product_id=product_id)
    except Exception:
        logger.exception("Failed to read cart item: user_id=%s product_id=%s", user_id, product_id)
        await callback.answer("⚠️ Не вдалося змінити кількість.", show_alert=True)
        return

    if current_item is None:
        await callback.answer("⚠️ Позицію вже видалено.", show_alert=True)
        cart = await _refresh_state_cart(state, user_id)
        await _update_cart_callback_message(callback, cart)
        return

    unit = str(current_item.unit)
    step = _cart_step(unit)
    new_quantity = float(current_item.quantity) + (delta * step)
    if unit == "шт":
        new_quantity = float(int(round(new_quantity)))

    try:
        if new_quantity <= 0:
            await cart_repository.delete_item(user_id=user_id, product_id=product_id)
        else:
            await cart_repository.upsert_item(
                user_id=user_id,
                product_id=product_id,
                product_name=str(current_item.product_name),
                quantity=new_quantity,
                price=float(current_item.price),
                unit=unit,
            )
        cart = await _refresh_state_cart(state, user_id)
    except Exception:
        logger.exception("Failed to update cart quantity: user_id=%s product_id=%s", user_id, product_id)
        await callback.answer("⚠️ Не вдалося змінити кількість.", show_alert=True)
        return

    await callback.answer("✅ Кількість оновлено.")
    await _update_cart_callback_message(callback, cart)


@router.callback_query(F.data == CART_CLEAR_CALLBACK)
async def cart_clear_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None:
        await callback.answer("⚠️ Не вдалося очистити кошик.", show_alert=True)
        return

    try:
        await cart_repository.clear_cart(user_id=user_id)
        cart = await _refresh_state_cart(state, user_id)
    except Exception:
        logger.exception("Failed to clear cart: user_id=%s", user_id)
        await callback.answer("⚠️ Не вдалося очистити кошик.", show_alert=True)
        return

    await callback.answer("✅ Кошик очищено.")
    await _update_cart_callback_message(callback, cart)


@router.message(OrderStates.waiting_for_client)
async def order_client_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    user_value = (message.text or "").strip()
    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити користувача. Спробуйте ще раз.")
        return

    data = await state.get_data()
    agent_id = str(data.get("agent_id", "")).strip() or (await _resolve_agent_id(user_id, state) or "")
    if not agent_id:
        await message.answer("⚠️ Не знайдено дані агента. Пройдіть /start ще раз.")
        return

    if user_value == SHOW_CART_BUTTON_TEXT:
        await _send_cart_preview(message, state, user_id)
        await _send_client_menu(message, state)
        return

    if user_value == BACK_BUTTON_TEXT:
        history_raw = data.get("client_parent_history", [])
        history = list(history_raw) if isinstance(history_raw, list) else []
        if not history:
            await _send_client_menu(message, state, text="Ви вже в кореневій папці клієнтів.")
            return

        previous_parent = history.pop()
        try:
            rows = await _fetch_clients_for_parent(
                user_id=user_id,
                agent_id=agent_id,
                parent_id=previous_parent,
            )
        except OneCServiceError as exc:
            logger.exception("Failed to fetch previous clients folder: user_id=%s", user_id)
            await message.answer(_service_unavailable_message(exc))
            return

        await _set_client_scope(
            state,
            parent_id=previous_parent,
            parent_history=history,
            rows=rows,
        )
        await _send_client_menu(message, state)
        return

    client_label_map = data.get("client_label_map")
    if not isinstance(client_label_map, dict) or user_value not in client_label_map:
        await message.answer("⚠️ Оберіть клієнта або папку з кнопок нижче.")
        await _send_client_menu(message, state)
        return

    selected_row = client_label_map[user_value]
    if bool(selected_row.get("is_folder")):
        current_parent = data.get("client_parent_id")
        history_raw = data.get("client_parent_history", [])
        history = list(history_raw) if isinstance(history_raw, list) else []
        history.append(current_parent if isinstance(current_parent, str) else None)
        new_parent_id = str(selected_row.get("id", "")).strip()

        try:
            rows = await _fetch_clients_for_parent(
                user_id=user_id,
                agent_id=agent_id,
                parent_id=new_parent_id,
            )
        except OneCServiceError as exc:
            logger.exception("Failed to fetch nested clients folder: user_id=%s", user_id)
            await message.answer(_service_unavailable_message(exc))
            return

        if not rows:
            await _send_client_menu(message, state, text="⚠️ У цій папці поки немає клієнтів.")
            return

        await _set_client_scope(
            state,
            parent_id=new_parent_id,
            parent_history=history,
            rows=rows,
        )
        await _send_client_menu(message, state, text=f"📁 Папка: <b>{selected_row.get('name', '-')}</b>\nОберіть далі:")
        return

    selected_client_id = str(selected_row.get("id", "")).strip()
    selected_client_name = str(selected_row.get("name", "")).strip() or "Клієнт"
    client_debt: float | None = None

    try:
        client_debt = await one_c_service.get_debt(
            client_id=selected_client_id,
            telegram_user_id=user_id,
        )
    except OneCServiceError as exc:
        logger.warning(
            "Failed to fetch client debt, debt will be shown as unavailable: user_id=%s client_id=%s error=%s",
            user_id,
            selected_client_id,
            str(exc),
        )

    try:
        contracts = await one_c_service.get_contracts(
            client_id=selected_client_id,
            telegram_user_id=user_id,
        )
    except OneCServiceError as exc:
        logger.exception("Failed to fetch contracts: user_id=%s client_id=%s", user_id, selected_client_id)
        await message.answer(_service_unavailable_message(exc))
        return

    serialized_contracts = _serialize_contracts(contracts)
    await state.set_state(OrderStates.waiting_for_contract)
    await state.update_data(
        selected_client=selected_client_name,
        selected_client_id=selected_client_id,
        selected_client_debt=client_debt,
        selected_contract=None,
        selected_contract_id=None,
        selected_price_type_id=None,
        product_parent_id=None,
        product_parent_history=[],
        current_products=[],
        product_labels=[],
        product_label_map={},
        awaiting_order_confirmation=False,
        order_submission_in_progress=False,
        comment=None,
    )
    await _set_contract_scope(state, serialized_contracts)

    if not serialized_contracts:
        await _send_contract_menu(
            message,
            state,
            text=(
                f"{_build_contract_prompt(selected_client_name, client_debt, suffix='')}\n"
                "⚠️ Для цього клієнта не знайдено договорів. Можна переглянути історію або повернутися назад."
            ),
        )
        return

    await _send_contract_menu(message, state, text=_build_contract_prompt(selected_client_name, client_debt))


@router.message(OrderStates.waiting_for_contract)
async def order_contract_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    user_value = (message.text or "").strip()
    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити користувача. Спробуйте ще раз.")
        return

    data = await state.get_data()
    selected_client_id = str(data.get("selected_client_id", "")).strip()
    selected_client_name = str(data.get("selected_client", "Клієнт")).strip() or "Клієнт"
    promo_only = bool(data.get("promo_only"))

    if user_value == SHOW_CART_BUTTON_TEXT:
        await _send_cart_preview(message, state, user_id)
        await _send_contract_menu(message, state)
        return

    if user_value == BACK_BUTTON_TEXT:
        await state.set_state(OrderStates.waiting_for_client)
        await _send_client_menu(
            message,
            state,
            text="🔥 Оберіть клієнта або папку для акційного замовлення:" if promo_only else "Оберіть клієнта або папку:",
        )
        return

    if user_value == LAST_ORDERS_BUTTON_TEXT:
        if not selected_client_id:
            await message.answer("⚠️ Спочатку оберіть клієнта.")
            return
        try:
            history = await one_c_service.get_orders_history(
                client_id=selected_client_id,
                telegram_user_id=user_id,
            )
        except OneCServiceError as exc:
            logger.exception("Failed to fetch order history: user_id=%s client_id=%s", user_id, selected_client_id)
            await message.answer(_service_unavailable_message(exc))
            return

        history_rows = _serialize_history_rows(history)
        await message.answer(_build_history_message(history_rows, selected_client_name))
        await _send_contract_menu(message, state)
        return

    contract_label_map = data.get("contract_label_map")
    if not isinstance(contract_label_map, dict) or user_value not in contract_label_map:
        await message.answer("⚠️ Оберіть договір з кнопок нижче.")
        await _send_contract_menu(message, state)
        return

    selected_contract_row = contract_label_map[user_value]
    selected_contract_id = str(selected_contract_row.get("id", "")).strip()
    selected_contract_name = str(selected_contract_row.get("name", "")).strip() or "Договір"
    selected_price_type_id = str(selected_contract_row.get("price_type_id", "")).strip()

    if not selected_contract_id or not selected_price_type_id:
        await message.answer("⚠️ Не вдалося прочитати дані договору. Оберіть інший договір.")
        await _send_contract_menu(message, state)
        return

    try:
        products = await _fetch_and_set_product_scope(
            state,
            user_id=user_id,
            price_type_id=selected_price_type_id,
            parent_id=None,
            parent_history=[],
            promo_only=promo_only,
        )
    except OneCServiceError as exc:
        logger.exception("Failed to fetch root products: user_id=%s", user_id)
        await message.answer(_service_unavailable_message(exc))
        return

    await state.set_state(OrderStates.waiting_for_product)
    await state.update_data(
        selected_contract=selected_contract_name,
        selected_contract_id=selected_contract_id,
        selected_price_type_id=selected_price_type_id,
        selected_product=None,
        selected_product_id=None,
        selected_unit=None,
        selected_price_per_unit=None,
        updating_existing_item=False,
    )

    if not products:
        await _send_product_menu(
            message,
            state,
            text="⚠️ Для цього договору не знайдено акційних товарів." if promo_only else "⚠️ Каталог товарів порожній для цього договору.",
        )
        return

    await _send_product_menu(
        message,
        state,
        text=(
            f"Договір: <b>{selected_contract_name}</b>\n"
            + ("Оберіть папку або акційний товар:" if promo_only else "Оберіть папку або товар:")
        ),
    )


@router.message(OrderStates.waiting_for_product)
async def order_product_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    user_value = (message.text or "").strip()
    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити користувача. Спробуйте ще раз.")
        return

    data = await state.get_data()
    selected_price_type_id = str(data.get("selected_price_type_id", "")).strip()
    promo_only = bool(data.get("promo_only"))
    if not selected_price_type_id:
        await state.set_state(OrderStates.waiting_for_contract)
        await _send_contract_menu(
            message,
            state,
            text="⚠️ Ціна не вказана. Дзвоніть у збут.\nОберіть договір для роботи з каталогом.",
        )
        return

    if user_value == SHOW_CART_BUTTON_TEXT:
        await _send_cart_preview(message, state, user_id)
        await _send_product_menu(message, state)
        return

    if user_value == BACK_BUTTON_TEXT:
        history_raw = data.get("product_parent_history", [])
        history = list(history_raw) if isinstance(history_raw, list) else []
        if history:
            previous_parent = history.pop()
            try:
                await _fetch_and_set_product_scope(
                    state,
                    user_id=user_id,
                    price_type_id=selected_price_type_id,
                    parent_id=previous_parent if isinstance(previous_parent, str) else None,
                    parent_history=history,
                    promo_only=promo_only,
                )
            except OneCServiceError as exc:
                logger.exception("Failed to fetch previous product folder: user_id=%s", user_id)
                await message.answer(_service_unavailable_message(exc))
                return
            await _send_product_menu(message, state)
            return

        await state.set_state(OrderStates.waiting_for_contract)
        await _send_contract_menu(message, state)
        return

    if user_value == FINISH_ORDER_BUTTON_TEXT:
        cart_raw = data.get("cart", [])
        cart = list(cart_raw) if isinstance(cart_raw, list) else []
        if not cart:
            await _send_product_menu(message, state, text="⚠️ Кошик порожній. Додайте хоча б один товар.")
            return

        await state.set_state(OrderStates.waiting_for_comment)
        await state.update_data(
            awaiting_order_confirmation=False,
            order_submission_in_progress=False,
            comment=None,
        )
        await message.answer(
            "🚚 Введіть коментар до замовлення або натисніть «Без коментаря».",
            reply_markup=build_skip_comment_keyboard(),
        )
        return

    product_label_map = data.get("product_label_map")
    if not isinstance(product_label_map, dict) or user_value not in product_label_map:
        await message.answer("⚠️ Оберіть товар або папку з кнопок нижче.")
        await _send_product_menu(message, state)
        return

    selected_row = product_label_map[user_value]
    if bool(selected_row.get("is_folder")):
        current_parent = data.get("product_parent_id")
        history_raw = data.get("product_parent_history", [])
        history = list(history_raw) if isinstance(history_raw, list) else []
        history.append(current_parent if isinstance(current_parent, str) else None)
        new_parent_id = str(selected_row.get("id", "")).strip()

        try:
            products = await _fetch_and_set_product_scope(
                state,
                user_id=user_id,
                price_type_id=selected_price_type_id,
                parent_id=new_parent_id,
                parent_history=history,
                promo_only=promo_only,
            )
        except OneCServiceError as exc:
            logger.exception("Failed to fetch nested products folder: user_id=%s", user_id)
            await message.answer(_service_unavailable_message(exc))
            return

        if not products:
            await _send_product_menu(
                message,
                state,
                text="⚠️ У цій папці поки немає акційних товарів." if promo_only else "⚠️ У цій папці поки немає товарів.",
            )
            return

        await _send_product_menu(message, state, text=f"📁 Папка: <b>{selected_row.get('name', '-')}</b>\nОберіть далі:")
        return

    selected_product_name = str(selected_row.get("name", "")).strip() or "Товар"
    selected_product_id = str(selected_row.get("id", "")).strip()
    selected_unit = str(selected_row.get("unit", "")).strip() or "шт"
    selected_price = _to_float(selected_row.get("price", 0), default=0.0)

    if selected_price <= 0:
        await message.answer("⚠️ Ціна не вказана. Дзвоніть у збут.")
        await _send_product_menu(message, state)
        return

    cart_raw = data.get("cart", [])
    cart = list(cart_raw) if isinstance(cart_raw, list) else []
    existing_item = _find_cart_item(cart, selected_product_id)

    await state.set_state(OrderStates.waiting_for_quantity)
    await state.update_data(
        selected_product=selected_product_name,
        selected_product_id=selected_product_id,
        selected_unit=selected_unit,
        selected_price_per_unit=selected_price,
        updating_existing_item=existing_item is not None,
    )

    if existing_item is not None:
        await message.answer(
            f"ℹ️ Товар <b>{selected_product_name}</b> вже у кошику: "
            f"{_format_quantity(existing_item.get('quantity', 0))} {selected_unit}.\n"
            "Введіть нову кількість, щоб оновити позицію.",
        )
        return

    await message.answer(f"Введіть кількість для <b>{selected_product_name}</b> ({selected_unit}).")


@router.message(OrderStates.waiting_for_quantity)
async def order_quantity_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    raw_quantity = (message.text or "").strip()
    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити користувача. Спробуйте ще раз.")
        return

    data = await state.get_data()
    selected_product = str(data.get("selected_product", "")).strip()
    selected_product_id = str(data.get("selected_product_id", "")).strip()
    selected_unit = str(data.get("selected_unit", "")).strip()
    selected_price_per_unit = _to_float(data.get("selected_price_per_unit", 0), default=0.0)
    selected_price_type_id = str(data.get("selected_price_type_id", "")).strip()
    current_parent_id = data.get("product_parent_id")
    parent_id = current_parent_id if isinstance(current_parent_id, str) else None
    parent_history_raw = data.get("product_parent_history", [])
    parent_history = list(parent_history_raw) if isinstance(parent_history_raw, list) else []
    promo_only = bool(data.get("promo_only"))
    is_update = bool(data.get("updating_existing_item"))

    if selected_price_per_unit <= 0 or not selected_price_type_id:
        await state.set_state(OrderStates.waiting_for_product)
        await state.update_data(
            selected_product=None,
            selected_product_id=None,
            selected_unit=None,
            selected_price_per_unit=None,
            updating_existing_item=False,
        )
        await message.answer("⚠️ Ціна не вказана. Дзвоніть у збут.")
        await _send_product_menu(message, state)
        return

    try:
        quantity = validate_quantity(raw_quantity, selected_unit)
    except QuantityValidationError as exc:
        await message.answer(f"⚠️ {exc}")
        return

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
        await message.answer("⚠️ Не вдалося зберегти товар у кошик. Спробуйте ще раз.")
        return

    if not selected_price_type_id:
        await message.answer("⚠️ Не знайдено тип цін для каталогу. Поверніться до вибору договору.")
        await state.set_state(OrderStates.waiting_for_contract)
        await _send_contract_menu(message, state)
        return

    try:
        await _fetch_and_set_product_scope(
            state,
            user_id=user_id,
            price_type_id=selected_price_type_id,
            parent_id=parent_id,
            parent_history=parent_history,
            promo_only=promo_only,
        )
    except OneCServiceError as exc:
        logger.exception("Failed to refresh product scope after quantity input: user_id=%s", user_id)
        await message.answer(_service_unavailable_message(exc))
        return

    await state.set_state(OrderStates.waiting_for_product)
    await state.update_data(
        cart=cart,
        selected_product=None,
        selected_product_id=None,
        selected_unit=None,
        selected_price_per_unit=None,
        updating_existing_item=False,
    )

    action_text = "✅ Кількість оновлено у кошику." if is_update else "✅ Товар додано в кошик."
    await _send_product_menu(
        message,
        state,
        text=(
            f"{action_text}\n{_format_cart_summary(cart)}\n"
            f"Оберіть наступний товар, «{SHOW_CART_BUTTON_TEXT}» або «{FINISH_ORDER_BUTTON_TEXT}»."),
    )


@router.message(OrderStates.waiting_for_comment)
async def order_comment_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    user_value = (message.text or "").strip()
    if user_id is None:
        await message.answer("⚠️ Не вдалося визначити користувача. Спробуйте ще раз.")
        return

    data = await state.get_data()
    awaiting_confirmation = bool(data.get("awaiting_order_confirmation"))

    if awaiting_confirmation:
        if user_value == CONFIRM_ORDER_BUTTON_TEXT:
            submit_lock = _get_submit_lock(user_id)
            if submit_lock.locked():
                await message.answer("⚠️ Замовлення вже відправляється. Зачекайте, будь ласка.")
                return

            async with submit_lock:
                fresh_data = await state.get_data()
                if not bool(fresh_data.get("awaiting_order_confirmation")):
                    await message.answer("ℹ️ Замовлення вже оброблено.")
                    return
                if bool(fresh_data.get("order_submission_in_progress")):
                    await message.answer("⚠️ Замовлення вже відправляється. Зачекайте, будь ласка.")
                    return

                is_flood, wait_seconds = _is_submit_flood(user_id)
                if is_flood:
                    await message.answer(
                        f"⚠️ Запит уже обробляється. Зачекайте {wait_seconds} сек. і повторіть підтвердження.",
                    )
                    return

                await state.update_data(order_submission_in_progress=True)
                order_payload = _build_create_order_payload(fresh_data)
                try:
                    result = await one_c_service.create_order(
                        order_payload,
                        telegram_user_id=user_id,
                    )
                except OneCServiceError as exc:
                    _last_submit_attempts.pop(user_id, None)
                    await state.update_data(order_submission_in_progress=False)
                    logger.exception("Order submit failed: user_id=%s", user_id)
                    await message.answer(_service_unavailable_message(exc))
                    return

                try:
                    await cart_repository.clear_cart(user_id)
                except Exception:
                    logger.exception("Failed to clear cart after order submit: user_id=%s", user_id)

                order_number = result.order_number
                await state.clear()
                await message.answer(
                    "✅ <b>Успішно!</b> Замовлення відправлено.\n"
                    f"🚚 Номер замовлення: <b>{order_number}</b>",
                    reply_markup=build_main_inline_menu(),
                )
                return

        if user_value == CANCEL_ORDER_BUTTON_TEXT:
            await state.set_state(OrderStates.waiting_for_product)
            await state.update_data(
                awaiting_order_confirmation=False,
                order_submission_in_progress=False,
            )
            await _send_product_menu(message, state, text="ℹ️ Підтвердження скасовано. Продовжуйте роботу з каталогом.")
            return

        await message.answer(
            "Оберіть дію: підтвердити або скасувати замовлення.",
            reply_markup=build_options_keyboard([CONFIRM_ORDER_BUTTON_TEXT, CANCEL_ORDER_BUTTON_TEXT]),
        )
        return

    if not user_value:
        await message.answer(
            "⚠️ Коментар не може бути порожнім. Введіть коментар або натисніть «Без коментаря».",
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
    summary = _build_order_summary(await state.get_data())
    await message.answer(
        f"{summary}\n\nПідтвердити відправку в 1С?",
        reply_markup=build_options_keyboard([CONFIRM_ORDER_BUTTON_TEXT, CANCEL_ORDER_BUTTON_TEXT]),
    )
