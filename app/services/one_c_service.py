import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

from config import get_settings

logger = logging.getLogger(__name__)
http_logger = logging.getLogger("one_c_http_requests")


class OneCServiceError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Client:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Product:
    id: str
    name: str
    unit: str
    category: str
    price_per_unit: float


@dataclass(frozen=True, slots=True)
class AuthAgent:
    agent_id: str
    name: str


class OneCService:
    def __init__(self, timeout_sec: int = 10) -> None:
        settings = get_settings()
        self.base_url = str(settings.one_c_base_url).rstrip("/") if settings.one_c_base_url else None
        self.username = settings.one_c_username
        self.password = settings.one_c_password.get_secret_value() if settings.one_c_password else None
        self.x_bot_token = (
            settings.one_c_x_bot_token.get_secret_value()
            if settings.one_c_x_bot_token is not None
            else None
        )
        self.timeout_sec = timeout_sec

    def _build_auth(self) -> aiohttp.BasicAuth:
        if not self.username or not self.password:
            raise OneCServiceError("Сервіс 1С не налаштований. Зверніться до адміністратора.")
        return aiohttp.BasicAuth(self.username, self.password)

    def _build_url(self, endpoint: str) -> str:
        if not self.base_url:
            raise OneCServiceError("Адреса сервісу 1С не налаштована.")
        clean_endpoint = endpoint.lstrip("/")
        if not clean_endpoint:
            return self.base_url
        return f"{self.base_url}/{clean_endpoint}"

    def _build_headers(
        self,
        *,
        auth: aiohttp.BasicAuth,
        user_id: int,
        has_body: bool,
    ) -> tuple[dict[str, str], dict[str, str]]:
        if not self.x_bot_token:
            raise OneCServiceError("Секретний ключ 1С не налаштований. Зверніться до адміністратора.")
        if user_id <= 0:
            raise OneCServiceError("Не вдалося визначити Telegram ID користувача.")

        headers: dict[str, str] = {
            "Accept": "application/json",
            "Authorization": auth.encode(),
            "X-Bot-Token": self.x_bot_token,
        }
        headers["X-Telegram-User-ID"] = str(user_id)
        if has_body:
            headers["Content-Type"] = "application/json"

        log_headers = {
            "Accept": "application/json",
            "Authorization": "Basic ***",
            "X-Bot-Token": "***",
            "X-Telegram-User-ID": str(user_id),
        }
        if has_body:
            log_headers["Content-Type"] = "application/json"
        return headers, log_headers

    @staticmethod
    def _truncate_body(body: str, max_len: int = 1000) -> str:
        if len(body) <= max_len:
            return body
        return f"{body[:max_len]}...<truncated>"

    @staticmethod
    def _pick_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    @classmethod
    def _extract_collection(
        cls,
        payload: Any,
        preferred_keys: tuple[str, ...],
        *,
        depth: int = 5,
    ) -> list[Any]:
        if depth < 0:
            return []

        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []

        for key in preferred_keys:
            if key in payload:
                candidate = cls._extract_collection(payload.get(key), preferred_keys, depth=depth - 1)
                if candidate:
                    return candidate

        for value in payload.values():
            candidate = cls._extract_collection(value, preferred_keys, depth=depth - 1)
            if candidate:
                return candidate
        return []

    @staticmethod
    def _parse_success_response(payload: Any) -> bool:
        if isinstance(payload, bool):
            return payload
        if isinstance(payload, dict):
            for key in ("success", "ok", "authorized", "is_authorized", "linked", "result"):
                value = payload.get(key)
                if isinstance(value, bool):
                    return value
                if isinstance(value, (str, int)):
                    normalized = str(value).strip().lower()
                    if normalized in {"1", "true", "yes", "ok", "success", "authorized", "linked"}:
                        return True
                    if normalized in {"0", "false", "no", "fail", "failed", "error"}:
                        return False

            status_raw = str(payload.get("status", "")).strip().lower()
            if status_raw in {"ok", "success", "authorized", "linked"}:
                return True
            if status_raw in {"fail", "failed", "error"}:
                return False

        if isinstance(payload, str):
            normalized = payload.strip().lower()
            if normalized in {"ok", "success", "authorized", "linked"}:
                return True
            if normalized in {"fail", "failed", "error"}:
                return False

        return False

    @staticmethod
    def _extract_uuid(text: str) -> str | None:
        match = re.search(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            text,
        )
        if not match:
            return None
        return match.group(0)

    @classmethod
    def _extract_agent_info(cls, payload: Any) -> tuple[str | None, str | None]:
        candidates: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            candidates.append(payload)
            for key in ("agent", "data", "result", "user", "payload"):
                value = payload.get(key)
                if isinstance(value, dict):
                    candidates.append(value)
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    candidates.append(item)

        for candidate in candidates:
            agent_id = cls._pick_str(
                candidate,
                ("agent_id", "agentId", "id", "uuid", "guid", "agent_uuid"),
            )
            agent_name = cls._pick_str(
                candidate,
                ("name", "agent_name", "agentName", "full_name", "fullname", "fio"),
            )
            if agent_id:
                return agent_id, agent_name

        if isinstance(payload, dict):
            raw_response = payload.get("raw_response")
            if isinstance(raw_response, str):
                agent_id = cls._extract_uuid(raw_response)
                if agent_id:
                    return agent_id, None

        return None, None

    @classmethod
    def _extract_order_number(cls, payload: Any) -> str | None:
        if isinstance(payload, dict):
            number = cls._pick_str(
                payload,
                ("order_number", "orderNumber", "number", "order_id", "id"),
            )
            if number:
                return number
            for key in ("data", "result", "order", "payload"):
                value = payload.get(key)
                if isinstance(value, dict):
                    nested = cls._pick_str(
                        value,
                        ("order_number", "orderNumber", "number", "order_id", "id"),
                    )
                    if nested:
                        return nested
        return None

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if isinstance(value, str):
                value = value.replace(",", ".")
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_unit(value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"kg", "кг", "кілограм", "килограмм"}:
            return "кг"
        if normalized in {"pcs", "pc", "шт", "штук", "штука"}:
            return "шт"
        return value.strip() or "шт"

    @classmethod
    def _normalize_order_payload(cls, order_data: dict[str, Any]) -> dict[str, Any]:
        raw_items = order_data.get("items")
        normalized_items: list[dict[str, Any]] = []

        if isinstance(raw_items, list):
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue

                product_id = str(
                    raw_item.get("product_id")
                    or raw_item.get("id")
                    or raw_item.get("product_uuid")
                    or "",
                ).strip()
                if not product_id:
                    continue

                product_name = str(
                    raw_item.get("product")
                    or raw_item.get("product_name")
                    or raw_item.get("name")
                    or "Товар"
                ).strip()
                unit = cls._normalize_unit(str(raw_item.get("unit", "")).strip())
                quantity_raw = raw_item.get("quantity", 0)
                quantity_float = cls._to_float(quantity_raw, default=0.0)
                if unit == "шт":
                    quantity: int | float = int(round(quantity_float))
                else:
                    quantity = quantity_float

                price_per_unit = cls._to_float(
                    raw_item.get("price_per_unit", raw_item.get("price", raw_item.get("unit_price", 0))),
                    default=0.0,
                )
                line_total = cls._to_float(raw_item.get("line_total"), default=float(quantity) * price_per_unit)

                normalized_items.append(
                    {
                        "id": product_id,
                        "product_id": product_id,
                        "product_name": product_name,
                        "quantity": quantity,
                        "unit": unit,
                        "price_per_unit": price_per_unit,
                        "line_total": line_total,
                    },
                )

        payload: dict[str, Any] = {
            "client_id": str(order_data.get("client_id", "")).strip(),
            "client_name": str(order_data.get("client_name", "")).strip(),
            "trading_point": str(order_data.get("trading_point", "")).strip(),
            "delivery_date": str(order_data.get("delivery_date", "")).strip(),
            "payment_method": str(order_data.get("payment_method", "")).strip(),
            "comment": order_data.get("comment"),
            "items": normalized_items,
        }
        return payload

    async def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        telegram_user_id: int,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = self._build_url(endpoint)
        auth = self._build_auth()
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        headers, log_headers = self._build_headers(
            auth=auth,
            user_id=telegram_user_id,
            has_body=payload is not None,
        )

        http_logger.info(
            "REQUEST | method=%s url=%s headers=%s params=%s body=%s",
            method,
            url,
            log_headers,
            params,
            payload,
        )

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    headers=headers,
                ) as response:
                    raw_body = await response.text()
                    http_logger.info(
                        "RESPONSE | method=%s url=%s status=%s body=%s",
                        method,
                        url,
                        response.status,
                        self._truncate_body(raw_body),
                    )

                    if response.status == 200:
                        if not raw_body.strip():
                            return {}
                        try:
                            return await response.json(content_type=None)
                        except aiohttp.ContentTypeError:
                            logger.warning("1C response is not JSON: endpoint=%s body=%s", endpoint, raw_body)
                            return {"raw_response": raw_body}

                    if response.status == 401:
                        raise OneCServiceError("Авторизація в сервісі 1С не пройшла.")

                    if response.status >= 500:
                        raise OneCServiceError("Сервіс 1С тимчасово недоступний. Спробуйте трохи пізніше.")

                    raise OneCServiceError(
                        "Сервіс 1С повернув неочікувану відповідь. Спробуйте ще раз пізніше.",
                    )
        except asyncio.TimeoutError as exc:
            http_logger.error("REQUEST FAILED | method=%s url=%s error=timeout", method, url)
            raise OneCServiceError("Сервіс 1С не відповідає. Спробуйте трохи пізніше.") from exc
        except aiohttp.ClientError as exc:
            http_logger.error(
                "REQUEST FAILED | method=%s url=%s error=client_error details=%s",
                method,
                url,
                str(exc),
            )
            raise OneCServiceError("Не вдалося з'єднатися із сервісом 1С.") from exc

    async def authorize_agent(self, phone: str, telegram_user_id: int) -> AuthAgent | None:
        masked_phone = f"***{phone[-4:]}" if len(phone) >= 4 else "***"
        logger.info("1C auth started: user_id=%s phone=%s", telegram_user_id, masked_phone)

        response = await self._request_json(
            "GET",
            "auth",
            telegram_user_id=telegram_user_id,
            params={"phone": phone},
        )

        is_success = self._parse_success_response(response)
        agent_id, agent_name = self._extract_agent_info(response)

        if not is_success and not agent_id:
            logger.warning("1C auth rejected: user_id=%s phone=%s", telegram_user_id, masked_phone)
            return None

        if not agent_id:
            raise OneCServiceError("1С не повернула agent_id після авторизації.")

        final_name = agent_name or "Агент"
        logger.info(
            "1C auth success: user_id=%s agent_id=%s agent_name=%s",
            telegram_user_id,
            agent_id,
            final_name,
        )
        return AuthAgent(agent_id=agent_id, name=final_name)

    async def get_clients(self, telegram_user_id: int) -> list[Client]:
        logger.info("Fetching clients from 1C: user_id=%s", telegram_user_id)
        response = await self._request_json("GET", "clients", telegram_user_id=telegram_user_id)
        rows = self._extract_collection(
            response,
            ("clients", "items", "data", "result", "rows", "list"),
        )
        if not rows and isinstance(response, dict):
            rows = [response]

        clients: list[Client] = []
        seen_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            client_id = self._pick_str(
                row,
                ("id", "client_id", "clientId", "uuid", "guid", "code", "ref"),
            )
            name = self._pick_str(
                row,
                ("name", "client_name", "clientName", "title", "description", "full_name", "fio"),
            )
            if not client_id or not name or client_id in seen_ids:
                continue
            seen_ids.add(client_id)
            clients.append(Client(id=client_id, name=name))
        return clients

    async def get_products(self, category: str, telegram_user_id: int) -> list[Product]:
        logger.info("Fetching products from 1C: user_id=%s", telegram_user_id)
        response = await self._request_json("GET", "products", telegram_user_id=telegram_user_id)
        rows = self._extract_collection(
            response,
            ("products", "items", "data", "result", "rows", "list", "goods", "nomenclature"),
        )
        if not rows and isinstance(response, dict):
            rows = [response]

        category_filter = category.strip().casefold()
        products: list[Product] = []
        seen_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue

            product_id = self._pick_str(
                row,
                ("id", "product_id", "productId", "uuid", "guid", "code", "ref"),
            )
            name = self._pick_str(
                row,
                ("name", "product_name", "productName", "title", "description"),
            )
            if not product_id or not name or product_id in seen_ids:
                continue

            category_value = (
                self._pick_str(row, ("category", "category_name", "group", "group_name", "type"))
                or "Інше"
            )
            if category_filter and category_value.casefold() != category_filter:
                continue

            unit = self._normalize_unit(self._pick_str(row, ("unit", "uom", "measure", "unit_name")) or "шт")
            raw_price = row.get("price_per_unit", row.get("price", row.get("unit_price", row.get("cost", 0))))
            price_per_unit = self._to_float(raw_price, default=0.0)

            seen_ids.add(product_id)
            products.append(
                Product(
                    id=product_id,
                    name=name,
                    unit=unit,
                    category=category_value,
                    price_per_unit=price_per_unit,
                ),
            )

        return products

    async def get_categories(self, telegram_user_id: int) -> list[str]:
        products = await self.get_products("", telegram_user_id=telegram_user_id)
        categories = sorted({product.category for product in products if product.category})
        return categories

    async def find_product(self, category: str, product_name: str, telegram_user_id: int) -> Product | None:
        products = await self.get_products(category, telegram_user_id=telegram_user_id)
        for product in products:
            if product.name == product_name:
                return product
        if category:
            all_products = await self.get_products("", telegram_user_id=telegram_user_id)
            for product in all_products:
                if product.name == product_name:
                    return product
        return None

    async def get_trading_points(self, client_id: str, telegram_user_id: int) -> list[str]:
        logger.info(
            "Fetching trading points from clients payload: client_id=%s user_id=%s",
            client_id,
            telegram_user_id,
        )
        response = await self._request_json("GET", "clients", telegram_user_id=telegram_user_id)
        rows = self._extract_collection(response, ("clients", "items", "data", "result", "rows", "list"))
        if not rows and isinstance(response, dict):
            rows = [response]

        candidate_client: dict[str, Any] | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = self._pick_str(row, ("id", "client_id", "clientId", "uuid", "guid", "code", "ref"))
            if row_id == client_id:
                candidate_client = row
                break

        if candidate_client is None:
            return ["Основна точка"]

        points: list[str] = []
        for key in ("trading_points", "tradingPoints", "points", "outlets", "stores", "shops"):
            value = candidate_client.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        text = item.strip()
                        if text:
                            points.append(text)
                    elif isinstance(item, dict):
                        point_name = self._pick_str(item, ("name", "title", "address", "point_name"))
                        if point_name:
                            points.append(point_name)
            elif isinstance(value, dict):
                point_name = self._pick_str(value, ("name", "title", "address", "point_name"))
                if point_name:
                    points.append(point_name)
            elif isinstance(value, str):
                text = value.strip()
                if text:
                    points.append(text)

        unique_points = list(dict.fromkeys(points))
        return unique_points or ["Основна точка"]

    async def create_order(self, order_data: dict[str, Any], telegram_user_id: int) -> dict[str, Any]:
        logger.info("Creating order in 1C: user_id=%s", telegram_user_id)
        payload = self._normalize_order_payload(order_data)
        if not payload.get("client_id"):
            raise OneCServiceError("Не вдалося сформувати замовлення: відсутній client_id.")
        if not payload.get("items"):
            raise OneCServiceError("Не вдалося сформувати замовлення: кошик порожній.")

        response = await self._request_json(
            "POST",
            "create_order",
            telegram_user_id=telegram_user_id,
            payload=payload,
        )

        order_number = self._extract_order_number(response)
        is_success = self._parse_success_response(response)
        if not order_number and not is_success:
            raise OneCServiceError("Не вдалося отримати підтвердження створення замовлення від 1С.")

        return {"status": "success", "order_number": order_number or "N/A"}

    async def check_base_url_status(self, telegram_user_id: int) -> tuple[bool, str]:
        ok, _, message = await self.check_base_url_get(telegram_user_id=telegram_user_id)
        return ok, message

    async def check_base_url_get(self, telegram_user_id: int) -> tuple[bool, int | None, str]:
        url = self._build_url("")
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        auth = self._build_auth()
        headers, log_headers = self._build_headers(
            auth=auth,
            user_id=telegram_user_id,
            has_body=False,
        )

        http_logger.info(
            "REQUEST | method=%s url=%s headers=%s params=%s body=%s",
            "GET",
            url,
            log_headers,
            None,
            None,
        )

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, allow_redirects=True, headers=headers) as response:
                    body = await response.text()
                    status = response.status
                    http_logger.info(
                        "RESPONSE | method=%s url=%s status=%s body=%s",
                        "GET",
                        url,
                        status,
                        self._truncate_body(body),
                    )
        except asyncio.TimeoutError:
            http_logger.error("REQUEST FAILED | method=%s url=%s error=timeout", "GET", url)
            return False, None, "Сервіс 1С не відповідає."
        except aiohttp.ClientError as exc:
            http_logger.error(
                "REQUEST FAILED | method=%s url=%s error=client_error details=%s",
                "GET",
                url,
                str(exc),
            )
            return False, None, "Не вдалося підключитися до сервісу 1С."

        if status == 200:
            return True, status, "Сервіс 1С доступний."
        if status == 401:
            return False, status, "Сервіс 1С відповідає, але авторизація не пройшла."
        if status >= 500:
            return False, status, "Сервіс 1С тимчасово недоступний."
        return True, status, f"Сервіс 1С відповідає (код {status})."
