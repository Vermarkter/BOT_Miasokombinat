import asyncio
import logging
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
    name: str
    unit: str
    category: str
    price_per_unit: float


class OneCService:
    def __init__(self, timeout_sec: int = 10) -> None:
        settings = get_settings()
        self.base_url = str(settings.one_c_base_url).rstrip("/") if settings.one_c_base_url else None
        self.username = settings.one_c_username
        self.password = settings.one_c_password.get_secret_value() if settings.one_c_password else None
        self.timeout_sec = timeout_sec

    def _build_auth(self) -> aiohttp.BasicAuth:
        if not self.username or not self.password:
            raise OneCServiceError("Сервіс замовлень не налаштований. Зверніться до адміністратора.")
        return aiohttp.BasicAuth(self.username, self.password)

    def _build_url(self, endpoint: str) -> str:
        if not self.base_url:
            raise OneCServiceError("Адреса сервісу замовлень не налаштована.")
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    @staticmethod
    def _get_log_headers(auth: aiohttp.BasicAuth | None, has_body: bool) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if has_body:
            headers["Content-Type"] = "application/json"
        if auth is not None:
            headers["Authorization"] = "Basic ***"
        return headers

    @staticmethod
    def _truncate_body(body: str, max_len: int = 1000) -> str:
        if len(body) <= max_len:
            return body
        return f"{body[:max_len]}...<truncated>"

    async def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = self._build_url(endpoint)
        auth = self._build_auth()
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)

        log_headers = self._get_log_headers(auth, payload is not None)
        http_logger.info(
            "REQUEST | method=%s url=%s headers=%s params=%s body=%s",
            method,
            url,
            log_headers,
            params,
            payload,
        )

        try:
            async with aiohttp.ClientSession(timeout=timeout, auth=auth) as session:
                async with session.request(method, url, params=params, json=payload) as response:
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
                            logger.error("1C response is not JSON: endpoint=%s body=%s", endpoint, raw_body)
                            raise OneCServiceError("Отримано некоректну відповідь від сервісу замовлень.")

                    if response.status == 401:
                        raise OneCServiceError("Не вдалося авторизуватися в сервісі замовлень.")

                    if response.status == 500:
                        raise OneCServiceError("Сервіс замовлень тимчасово недоступний. Спробуйте пізніше.")

                    raise OneCServiceError(
                        "Сервіс замовлень повернув неочікувану відповідь. Спробуйте пізніше.",
                    )
        except asyncio.TimeoutError as exc:
            http_logger.error("REQUEST FAILED | method=%s url=%s error=timeout", method, url)
            raise OneCServiceError("Сервіс замовлень не відповідає. Спробуйте трохи пізніше.") from exc
        except aiohttp.ClientError as exc:
            http_logger.error(
                "REQUEST FAILED | method=%s url=%s error=client_error details=%s",
                method,
                url,
                str(exc),
            )
            raise OneCServiceError("Не вдалося з'єднатися із сервісом замовлень.") from exc

    async def check_auth(self, phone: str, code: str) -> bool:
        masked_phone = f"***{phone[-4:]}" if len(phone) >= 4 else "***"
        logger.info("1C auth check started for phone=%s", masked_phone)
        response = await self._request_json(
            "POST",
            "auth/check",
            payload={"phone": phone, "code": code},
        )
        is_authorized = bool(response.get("authorized"))
        if is_authorized:
            logger.info("1C auth check success for phone=%s", masked_phone)
        else:
            logger.warning("1C auth check failed for phone=%s", masked_phone)
        return is_authorized

    async def get_clients(self) -> list[Client]:
        logger.info("Fetching clients from 1C endpoint")
        response = await self._request_json("GET", "clients")
        rows = response.get("clients", response if isinstance(response, list) else [])
        clients: list[Client] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            client_id = str(row.get("id", "")).strip()
            name = str(row.get("name", "")).strip()
            if client_id and name:
                clients.append(Client(id=client_id, name=name))
        return clients

    async def get_categories(self) -> list[str]:
        logger.info("Fetching categories from 1C endpoint")
        response = await self._request_json("GET", "categories")
        rows = response.get("categories", response if isinstance(response, list) else [])
        categories = [str(item).strip() for item in rows if str(item).strip()]
        return categories

    async def get_products(self, category: str) -> list[Product]:
        logger.info("Fetching products from 1C endpoint for category=%s", category)
        response = await self._request_json("GET", "products", params={"category": category})
        rows = response.get("products", response if isinstance(response, list) else [])
        products: list[Product] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            unit = str(row.get("unit", "")).strip()
            category_value = str(row.get("category", category)).strip()
            price = row.get("price_per_unit", 0)
            if not name or not unit:
                continue
            try:
                price_per_unit = float(price)
            except (TypeError, ValueError):
                price_per_unit = 0.0
            products.append(
                Product(
                    name=name,
                    unit=unit,
                    category=category_value,
                    price_per_unit=price_per_unit,
                ),
            )
        return products

    async def find_product(self, category: str, product_name: str) -> Product | None:
        products = await self.get_products(category)
        for product in products:
            if product.name == product_name:
                return product
        return None

    async def get_trading_points(self, client_id: str) -> list[str]:
        logger.info("Fetching trading points from 1C endpoint for client_id=%s", client_id)
        response = await self._request_json("GET", "trading-points", params={"client_id": client_id})
        rows = response.get("trading_points", response if isinstance(response, list) else [])
        return [str(item).strip() for item in rows if str(item).strip()]

    async def create_order(self, order_data: dict[str, Any]) -> dict[str, Any]:
        logger.info("Creating order in 1C endpoint")
        response = await self._request_json("POST", "orders", payload=order_data)
        order_number = str(response.get("order_number", "")).strip()
        if not order_number:
            raise OneCServiceError("Не вдалося отримати номер замовлення від сервісу.")
        return {"status": "success", "order_number": order_number}

    async def check_base_url_status(self) -> tuple[bool, str]:
        ok, _, message = await self.check_base_url_get()
        return ok, message

    async def check_base_url_get(self) -> tuple[bool, int | None, str]:
        url = self._build_url("")
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)

        auth: aiohttp.BasicAuth | None = None
        if self.username and self.password:
            auth = aiohttp.BasicAuth(self.username, self.password)

        log_headers = self._get_log_headers(auth, has_body=False)
        http_logger.info(
            "REQUEST | method=%s url=%s headers=%s params=%s body=%s",
            "GET",
            url,
            log_headers,
            None,
            None,
        )

        try:
            async with aiohttp.ClientSession(timeout=timeout, auth=auth) as session:
                async with session.get(url, allow_redirects=True) as response:
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
