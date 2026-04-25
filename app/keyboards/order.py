from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

PAYMENT_METHOD_CASH = "Готівка"
PAYMENT_METHOD_NON_CASH = "Безготівка"
NO_COMMENT_BUTTON_TEXT = "Без коментаря"
SHOW_CART_BUTTON_TEXT = "Мій кошик"

CLIENT_PAGE_PREV_CB = "cl<"
CLIENT_PAGE_NEXT_CB = "cl>"
CLIENT_UP_CB = "clu"
CLIENT_CART_CB = "clc"

PRODUCT_PAGE_PREV_CB = "pr<"
PRODUCT_PAGE_NEXT_CB = "pr>"
PRODUCT_UP_CB = "pru"
PRODUCT_CART_CB = "prc"
PRODUCT_FINISH_CB = "prf"

DEFAULT_LIST_PAGE_SIZE = 15


def build_options_keyboard(options: list[str], extra_buttons: list[str] | None = None) -> ReplyKeyboardMarkup:
    keyboard_rows = [[KeyboardButton(text=option)] for option in options]

    if extra_buttons:
        keyboard_rows.extend([[KeyboardButton(text=button)] for button in extra_buttons])

    return ReplyKeyboardMarkup(
        keyboard=keyboard_rows,
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_nearest_delivery_dates(days: int = 5) -> list[str]:
    start = date.today() + timedelta(days=1)
    return [(start + timedelta(days=idx)).strftime("%Y-%m-%d") for idx in range(days)]


def build_delivery_dates_keyboard(days: int = 5) -> ReplyKeyboardMarkup:
    return build_options_keyboard(get_nearest_delivery_dates(days))


def get_payment_methods() -> list[str]:
    return [PAYMENT_METHOD_CASH, PAYMENT_METHOD_NON_CASH]


def build_payment_methods_keyboard() -> ReplyKeyboardMarkup:
    return build_options_keyboard(get_payment_methods())


def build_skip_comment_keyboard() -> ReplyKeyboardMarkup:
    return build_options_keyboard([NO_COMMENT_BUTTON_TEXT])


def build_clients_kb(
    rows: list[dict[str, str | bool | float]],
    *,
    page: int,
    page_size: int = DEFAULT_LIST_PAGE_SIZE,
    can_go_up: bool = False,
) -> InlineKeyboardMarkup:
    page_size = max(1, page_size)
    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * page_size
    end = start + page_size
    page_rows = rows[start:end]

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for row in page_rows:
        row_id = str(row.get("id", "")).strip()
        if not row_id:
            continue
        row_name = str(row.get("name", "")).strip() or "Клієнт"
        is_folder = bool(row.get("is_folder"))
        label = f"📁 {row_name}" if is_folder else row_name
        # Keep callback_data minimal: only item id (UUID/reference).
        keyboard_rows.append([InlineKeyboardButton(text=label, callback_data=row_id)])

    nav_row: list[InlineKeyboardButton] = []
    if total_pages > 1 and safe_page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=CLIENT_PAGE_PREV_CB))
    if total_pages > 1 and safe_page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=CLIENT_PAGE_NEXT_CB))
    if nav_row:
        keyboard_rows.append(nav_row)

    action_row: list[InlineKeyboardButton] = [InlineKeyboardButton(text="🧺 Кошик", callback_data=CLIENT_CART_CB)]
    if can_go_up:
        action_row.insert(0, InlineKeyboardButton(text="↩️ Вгору", callback_data=CLIENT_UP_CB))
    keyboard_rows.append(action_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def build_products_kb(
    rows: list[dict[str, str | bool | float]],
    *,
    page: int,
    has_cart: bool,
    page_size: int = DEFAULT_LIST_PAGE_SIZE,
    can_go_up: bool = False,
) -> InlineKeyboardMarkup:
    page_size = max(1, page_size)
    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * page_size
    end = start + page_size
    page_rows = rows[start:end]

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for row in page_rows:
        row_id = str(row.get("id", "")).strip()
        if not row_id:
            continue
        row_name = str(row.get("name", "")).strip() or "Товар"
        is_folder = bool(row.get("is_folder"))
        if is_folder:
            label = f"📁 {row_name}"
        else:
            price = float(row.get("price", 0.0))
            label = f"{row_name} — {price:.2f} грн"
        # Keep callback_data minimal: only item id (UUID/reference).
        keyboard_rows.append([InlineKeyboardButton(text=label, callback_data=row_id)])

    nav_row: list[InlineKeyboardButton] = []
    if total_pages > 1 and safe_page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=PRODUCT_PAGE_PREV_CB))
    if total_pages > 1 and safe_page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=PRODUCT_PAGE_NEXT_CB))
    if nav_row:
        keyboard_rows.append(nav_row)

    action_row: list[InlineKeyboardButton] = []
    if can_go_up:
        action_row.append(InlineKeyboardButton(text="↩️ Вгору", callback_data=PRODUCT_UP_CB))
    action_row.append(InlineKeyboardButton(text="🧺 Кошик", callback_data=PRODUCT_CART_CB))
    if has_cart:
        action_row.append(InlineKeyboardButton(text="✅ Оформити", callback_data=PRODUCT_FINISH_CB))
    keyboard_rows.append(action_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def build_cart_inline_keyboard(cart_rows: list[dict[str, str]]) -> InlineKeyboardMarkup:
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for row in cart_rows:
        product_id = row["product_id"]
        product_name = row["product_name"]
        quantity = row["quantity"]
        price = row["price"]
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=f"🥩 {product_name} • {quantity} • {price}",
                    callback_data="cart:noop",
                ),
            ],
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(text="➖", callback_data=f"cart:minus:{product_id}"),
                InlineKeyboardButton(text="➕", callback_data=f"cart:plus:{product_id}"),
                InlineKeyboardButton(text="❌ Видалити", callback_data=f"cart:delete:{product_id}"),
            ],
        )

    if cart_rows:
        keyboard_rows.append(
            [
                InlineKeyboardButton(text="🧹 Очистити весь кошик", callback_data="cart:clear"),
            ],
        )
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
