import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from config import get_settings

logger = logging.getLogger(__name__)


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
    def __init__(self, timeout_sec: int = 15) -> None:
        settings = get_settings()
        self.base_url = str(settings.one_c_base_url).rstrip("/") if settings.one_c_base_url else None
        self.username = settings.one_c_username
        self.password = settings.one_c_password.get_secret_value() if settings.one_c_password else None
        self.timeout_sec = timeout_sec

    def _build_auth(self) -> aiohttp.BasicAuth:
        if not self.username or not self.password:
            raise OneCServiceError("ONE_C_USERNAME або ONE_C_PASSWORD не налаштовано.")
        return aiohttp.BasicAuth(self.username, self.password)

    def _build_url(self, endpoint: str) -> str:
        if not self.base_url:
            raise OneCServiceError("ONE_C_BASE_URL не налаштовано.")
        return f"{self.base_url}/{endpoint.lstrip('/')}"

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

        try:
            async with aiohttp.ClientSession(timeout=timeout, auth=auth) as session:
                async with session.request(method, url, params=params, json=payload) as response:
                    raw_body = await response.text()

                    if response.status == 200:
                        if not raw_body.strip():
                            return {}
                        try:
                            return await response.json(content_type=None)
                        except aiohttp.ContentTypeError:
                            logger.error("1C response is not JSON: endpoint=%s body=%s", endpoint, raw_body)
                            raise OneCServiceError("1C повернула невалідний JSON.")

                    if response.status == 401:
                        logger.error("1C returned 401 Unauthorized for endpoint=%s", endpoint)
                        raise OneCServiceError("Помилка авторизації в 1С (401).")

                    if response.status == 500:
                        logger.error("1C returned 500 Server Error for endpoint=%s body=%s", endpoint, raw_body)
                        raise OneCServiceError("Внутрішня помилка сервера 1С (500).")

                    logger.error(
                        "1C returned unexpected status: endpoint=%s status=%s body=%s",
                        endpoint,
                        response.status,
                        raw_body,
                    )
                    raise OneCServiceError(f"Неочікуваний HTTP-статус 1С: {response.status}.")
        except asyncio.TimeoutError as exc:
            logger.exception("1C request timeout: endpoint=%s", endpoint)
            raise OneCServiceError("Таймаут з'єднання з 1С.") from exc
        except aiohttp.ClientError as exc:
            logger.exception("1C request failed: endpoint=%s", endpoint)
            raise OneCServiceError("1С недоступна або помилка мережі.") from exc

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
            raise OneCServiceError("1C не повернула номер замовлення.")
        return {"status": "success", "order_number": order_number}
