import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from app.utils import normalize_phone
from config import settings as app_settings

logger = logging.getLogger(__name__)
http_logger = logging.getLogger("one_c_http_requests")


class OneCServiceError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AuthAgent:
    agent_id: str
    name: str


@dataclass(frozen=True, slots=True)
class Client:
    id: str
    name: str
    is_folder: bool


@dataclass(frozen=True, slots=True)
class Contract:
    id: str
    name: str
    price_type_id: str


@dataclass(frozen=True, slots=True)
class Product:
    id: str
    name: str
    is_folder: bool
    unit: str
    price: float
    is_promotional: bool = False


@dataclass(frozen=True, slots=True)
class OrderResponse:
    status: str
    order_number: str


@dataclass(frozen=True, slots=True)
class OrderHistoryItem:
    order_number: str
    date: str
    total: float


class OneCService:
    NOT_FOUND_MESSAGE = "Агента не знайдено в базі 1С"

    def __init__(self, timeout_sec: int | None = None) -> None:
        self.base_url = str(app_settings.one_c_base_url).rstrip("/") if app_settings.one_c_base_url else None
        self.username = app_settings.one_c_username
        self.password = app_settings.one_c_password.get_secret_value() if app_settings.one_c_password else None
        self.x_bot_token = (
            app_settings.one_c_x_bot_token.get_secret_value()
            if app_settings.one_c_x_bot_token is not None
            else None
        )
        self.mock_mode = app_settings.mock_mode
        self.mock_mode_on_1c_failure = app_settings.mock_mode_on_1c_failure
        self.timeout_sec = timeout_sec if timeout_sec is not None else app_settings.one_c_timeout

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
            "X-Telegram-User-ID": str(user_id),
        }
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

    @staticmethod
    def _pick_float(payload: dict[str, Any], keys: tuple[str, ...], *, default: float = 0.0) -> float:
        for key in keys:
            if key not in payload:
                continue
            value = payload.get(key)
            try:
                if isinstance(value, str):
                    value = value.replace(",", ".")
                return float(value)
            except (TypeError, ValueError):
                continue
        return default

    @classmethod
    def _pick_bool(cls, payload: dict[str, Any], keys: tuple[str, ...], *, default: bool = False) -> bool:
        for key in keys:
            if key not in payload:
                continue
            value = payload.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, int):
                return value != 0
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "y", "folder", "group"}:
                    return True
                if normalized in {"0", "false", "no", "n", "item"}:
                    return False
        return default

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

    @classmethod
    def _parse_success_response(cls, payload: Any) -> bool:
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

    @classmethod
    def _contains_not_found_status(cls, payload: Any, *, depth: int = 4) -> bool:
        if depth < 0:
            return False
        if isinstance(payload, dict):
            status = payload.get("status")
            if isinstance(status, str) and status.strip().lower() == "not_found":
                return True
            for value in payload.values():
                if cls._contains_not_found_status(value, depth=depth - 1):
                    return True
        elif isinstance(payload, list):
            for item in payload:
                if cls._contains_not_found_status(item, depth=depth - 1):
                    return True
        return False

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
        return None, None

    @classmethod
    def _extract_order_number(cls, payload: Any) -> str | None:
        if isinstance(payload, dict):
            number = cls._pick_str(payload, ("order_number", "orderNumber", "number", "order_id", "id", "code"))
            if number:
                return number
            for key in ("data", "result", "order", "payload"):
                value = payload.get(key)
                if isinstance(value, dict):
                    nested = cls._pick_str(
                        value,
                        ("order_number", "orderNumber", "number", "order_id", "id", "code"),
                    )
                    if nested:
                        return nested
        return None

    @staticmethod
    def _normalize_phone_digits(phone: str) -> str:
        normalized = normalize_phone(phone)
        source = normalized if normalized else phone
        return "".join(ch for ch in source if ch.isdigit())

    @staticmethod
    def _normalize_quantity(value: Any) -> int | float:
        try:
            quantity = float(value)
        except (TypeError, ValueError):
            quantity = 0.0
        if quantity.is_integer():
            return int(quantity)
        return quantity

    @staticmethod
    def _normalize_unit(value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"kg", "кг", "кілограм", "килограмм"}:
            return "кг"
        if normalized in {"pcs", "pc", "шт", "штук", "штука", "piece"}:
            return "шт"
        return value.strip() or "шт"

    @staticmethod
    def _looks_promotional_price(price: float) -> bool:
        return price > 0 and round(price % 1, 2) == 0.99

    @classmethod
    def _is_promotional_product_payload(cls, payload: dict[str, Any], *, price: float, name: str) -> bool:
        if cls._pick_bool(
            payload,
            ("is_promotional", "isPromotional", "promo", "is_promo", "promotion", "is_action"),
            default=False,
        ):
            return True

        normalized_name = name.casefold()
        if any(token in normalized_name for token in ("акція", "акц", "новинка", "promo", "sale", "new", "🔥")):
            return True

        return cls._looks_promotional_price(price)

    @classmethod
    def _extract_debt_amount(cls, payload: Any) -> float:
        if isinstance(payload, (int, float)):
            return float(payload)

        if isinstance(payload, str):
            return cls._pick_float({"value": payload}, ("value",), default=0.0)

        if isinstance(payload, list):
            for item in payload:
                amount = cls._extract_debt_amount(item)
                if amount:
                    return amount
            return 0.0

        if not isinstance(payload, dict):
            return 0.0

        direct_amount = cls._pick_float(
            payload,
            ("debt", "client_debt", "current_debt", "balance", "saldo", "amount", "sum", "total"),
            default=0.0,
        )
        if direct_amount:
            return direct_amount

        for key in ("data", "result", "payload", "client", "debt_info"):
            value = payload.get(key)
            if value is None:
                continue
            amount = cls._extract_debt_amount(value)
            if amount:
                return amount

        return 0.0

    def _can_use_mock(self, error: OneCServiceError) -> bool:
        if self.mock_mode:
            return True
        if not self.mock_mode_on_1c_failure:
            return False
        return str(error).strip() != self.NOT_FOUND_MESSAGE

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

                    parsed_body: Any
                    clean_text = raw_body.lstrip("\ufeff")
                    if not clean_text.strip():
                        parsed_body = {}
                    else:
                        try:
                            parsed_body = json.loads(clean_text)
                        except json.JSONDecodeError as exc:
                            logger.error("Помилка парсингу JSON від 1С: %s. Текст: %s", exc, raw_body[:100])
                            raise OneCServiceError("Некоректна відповідь від сервера 1С") from exc

                    if self._contains_not_found_status(parsed_body):
                        raise OneCServiceError(self.NOT_FOUND_MESSAGE)

                    if response.status == 200:
                        return parsed_body
                    if response.status == 401:
                        raise OneCServiceError("Авторизація в сервісі 1С не пройшла.")
                    if response.status >= 500:
                        raise OneCServiceError("Сервіс 1С тимчасово недоступний. Спробуйте трохи пізніше.")
                    raise OneCServiceError("Сервіс 1С повернув неочікувану відповідь. Спробуйте ще раз пізніше.")
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

    @staticmethod
    def _mock_agent(telegram_user_id: int) -> AuthAgent:
        return AuthAgent(agent_id=f"demo-agent-{telegram_user_id}", name="Демо Агент")

    @staticmethod
    def _mock_clients(parent_id: str | None) -> list[Client]:
        mapping: dict[str | None, list[Client]] = {
            None: [
                Client(id="demo-folder-stores", name="Демо магазини", is_folder=True),
                Client(id="demo-client-1", name='Магазин "Ромашка"', is_folder=False),
                Client(id="demo-client-2", name="Тестовий Магазин №2", is_folder=False),
            ],
            "demo-folder-stores": [
                Client(id="demo-client-3", name="Тестовий Магазин №1", is_folder=False),
            ],
        }
        return mapping.get(parent_id, [])

    @staticmethod
    def _mock_contracts(client_id: str) -> list[Contract]:
        _ = client_id
        return [
            Contract(id="demo-contract-main", name="Тестовий договір (Основний)", price_type_id="demo-price-main"),
            Contract(id="demo-contract-promo", name="Тестовий договір (Акційний)", price_type_id="demo-price-promo"),
        ]

    @staticmethod
    def _mock_products(price_type_id: str, parent_id: str | None) -> list[Product]:
        key = (price_type_id, parent_id)
        mapping: dict[tuple[str, str | None], list[Product]] = {
            ("demo-price-main", None): [
                Product(id="demo-folder-sausage", name="Ковбаси", is_folder=True, unit="шт", price=0.0),
                Product(id="demo-folder-sausage-child", name="Сосиски", is_folder=True, unit="шт", price=0.0),
                Product(id="demo-product-doc", name='Ковбаса "Докторська"', is_folder=False, unit="кг", price=238.5),
                Product(id="demo-product-kids", name='Сосиски "Дитячі"', is_folder=False, unit="шт", price=95.0),
            ],
            ("demo-price-main", "demo-folder-sausage"): [
                Product(id="demo-product-doc", name='Ковбаса "Докторська"', is_folder=False, unit="кг", price=238.5),
                Product(id="demo-product-salami", name="Салямі Фірмова", is_folder=False, unit="кг", price=320.0),
            ],
            ("demo-price-main", "demo-folder-sausage-child"): [
                Product(id="demo-product-kids", name='Сосиски "Дитячі"', is_folder=False, unit="шт", price=95.0),
            ],
            ("demo-price-promo", None): [
                Product(
                    id="demo-product-doc-promo",
                    name='Ковбаса "Докторська" (акція)',
                    is_folder=False,
                    unit="кг",
                    price=210.0,
                    is_promotional=True,
                ),
                Product(
                    id="demo-product-kids-promo",
                    name='Сосиски "Дитячі" (акція)',
                    is_folder=False,
                    unit="шт",
                    price=79.0,
                    is_promotional=True,
                ),
            ],
        }
        return mapping.get(key, mapping.get((price_type_id, None), []))

    @staticmethod
    def _mock_history(client_id: str) -> list[OrderHistoryItem]:
        client_suffix = client_id[-3:] if len(client_id) >= 3 else client_id
        today = datetime.now().date()
        return [
            OrderHistoryItem(
                order_number=f"DEMO-{client_suffix}-001",
                date=f"{today.isoformat()} 10:15",
                total=1250.0,
            ),
            OrderHistoryItem(
                order_number=f"DEMO-{client_suffix}-002",
                date=f"{today.isoformat()} 14:40",
                total=930.5,
            ),
            OrderHistoryItem(
                order_number=f"DEMO-{client_suffix}-003",
                date=f"{(today - timedelta(days=1)).isoformat()} 16:20",
                total=780.0,
            ),
        ]

    @staticmethod
    def _mock_debt(client_id: str) -> float:
        mapping = {
            "demo-client-1": 1250.0,
            "demo-client-2": 0.0,
            "demo-client-3": 340.5,
        }
        return mapping.get(client_id, 0.0)

    async def authorize_agent(self, phone: str, telegram_user_id: int) -> AuthAgent | None:
        phone_digits = self._normalize_phone_digits(phone)
        masked_phone = f"***{phone_digits[-4:]}" if len(phone_digits) >= 4 else "***"
        logger.info("1C auth started: user_id=%s phone=%s", telegram_user_id, masked_phone)

        if len(phone_digits) < 10:
            raise OneCServiceError("Некоректний номер телефону для авторизації.")

        if self.mock_mode:
            logger.info("Mock mode enabled: authorize_agent user_id=%s", telegram_user_id)
            return self._mock_agent(telegram_user_id)

        try:
            response = await self._request_json(
                "GET",
                "auth",
                telegram_user_id=telegram_user_id,
                params={"phone": phone_digits},
            )
        except OneCServiceError as exc:
            if self._can_use_mock(exc):
                logger.warning("1C unavailable during auth, switching to mock mode: reason=%s", str(exc))
                return self._mock_agent(telegram_user_id)
            raise

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

    async def get_clients(
        self,
        *,
        agent_id: str,
        telegram_user_id: int,
        parent_id: str | None = None,
    ) -> list[Client]:
        if self.mock_mode:
            logger.info("Mock mode enabled: get_clients user_id=%s parent_id=%s", telegram_user_id, parent_id)
            return self._mock_clients(parent_id)

        clean_agent_id = agent_id.strip()
        if not clean_agent_id:
            raise OneCServiceError("Не вдалося визначити agent_id для запиту клієнтів.")

        clean_parent_id = parent_id.strip() if isinstance(parent_id, str) else None
        params: dict[str, Any] = {
            "agent_id": clean_agent_id,
        }
        # For the first level, parent_id is omitted as agreed with 1C.
        if clean_parent_id:
            params["parent_id"] = clean_parent_id

        try:
            response = await self._request_json(
                "GET",
                "clients",
                telegram_user_id=telegram_user_id,
                params=params,
            )
        except OneCServiceError as exc:
            if self._can_use_mock(exc):
                logger.warning("1C unavailable in get_clients, using mock mode: reason=%s", str(exc))
                return self._mock_clients(parent_id)
            raise

        rows = self._extract_collection(response, ("clients", "items", "data", "result", "rows", "list"))
        if not rows and isinstance(response, dict):
            rows = [response]

        clients: list[Client] = []
        seen_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            client_id = self._pick_str(row, ("id", "client_id", "clientId", "uuid", "guid", "ref", "code"))
            name = self._pick_str(
                row,
                ("name", "client_name", "clientName", "title", "description", "full_name", "fio"),
            )
            if not client_id or not name or client_id in seen_ids:
                continue
            is_folder = self._pick_bool(
                row,
                ("is_folder", "isFolder", "folder", "is_group", "isGroup", "group"),
                default=False,
            )
            seen_ids.add(client_id)
            clients.append(Client(id=client_id, name=name, is_folder=is_folder))

        clients.sort(key=lambda item: (not item.is_folder, item.name.casefold()))
        return clients

    async def get_contracts(self, *, client_id: str, telegram_user_id: int) -> list[Contract]:
        if self.mock_mode:
            logger.info("Mock mode enabled: get_contracts user_id=%s client_id=%s", telegram_user_id, client_id)
            return self._mock_contracts(client_id)

        try:
            response = await self._request_json(
                "GET",
                "contracts",
                telegram_user_id=telegram_user_id,
                params={"client_id": client_id},
            )
        except OneCServiceError as exc:
            if self._can_use_mock(exc):
                logger.warning("1C unavailable in get_contracts, using mock mode: reason=%s", str(exc))
                return self._mock_contracts(client_id)
            raise

        rows = self._extract_collection(response, ("contracts", "items", "data", "result", "rows", "list"))
        if not rows and isinstance(response, dict):
            rows = [response]

        contracts: list[Contract] = []
        seen_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            contract_id = self._pick_str(
                row,
                ("id", "contract_id", "contractId", "uuid", "guid", "ref", "code"),
            )
            name = self._pick_str(
                row,
                ("name", "contract_name", "contractName", "title", "description", "number"),
            )
            price_type_id = self._pick_str(
                row,
                ("price_type_id", "priceTypeId", "price_type_uuid", "type_price_id", "priceType"),
            )
            if not price_type_id:
                price_type = row.get("price_type")
                if isinstance(price_type, dict):
                    price_type_id = self._pick_str(price_type, ("id", "uuid", "guid", "ref", "code"))

            if not contract_id or not price_type_id or contract_id in seen_ids:
                continue
            seen_ids.add(contract_id)
            contracts.append(
                Contract(
                    id=contract_id,
                    name=name or f"Договір {contract_id[:8]}",
                    price_type_id=price_type_id,
                ),
            )

        contracts.sort(key=lambda item: item.name.casefold())
        return contracts

    async def get_debt(self, client_id: str, telegram_user_id: int) -> float:
        if self.mock_mode:
            logger.info("Mock mode enabled: get_debt user_id=%s client_id=%s", telegram_user_id, client_id)
            return self._mock_debt(client_id)

        try:
            response = await self._request_json(
                "GET",
                "debt",
                telegram_user_id=telegram_user_id,
                params={"client_id": client_id},
            )
            return self._extract_debt_amount(response)
        except OneCServiceError as exc:
            if self._can_use_mock(exc):
                logger.warning("1C unavailable in get_debt, using mock mode: reason=%s", str(exc))
                return self._mock_debt(client_id)
            raise

    async def get_client_debt(self, client_id: str, telegram_user_id: int) -> float:
        # Backward compatibility alias.
        return await self.get_debt(client_id=client_id, telegram_user_id=telegram_user_id)

    async def get_products(
        self,
        *,
        price_type_id: str,
        telegram_user_id: int,
        parent_id: str | None = None,
    ) -> list[Product]:
        if self.mock_mode:
            logger.info(
                "Mock mode enabled: get_products user_id=%s price_type_id=%s parent_id=%s",
                telegram_user_id,
                price_type_id,
                parent_id,
            )
            return self._mock_products(price_type_id, parent_id)

        params: dict[str, Any] = {"price_type_id": price_type_id}
        if parent_id:
            params["parent_id"] = parent_id

        try:
            response = await self._request_json(
                "GET",
                "products",
                telegram_user_id=telegram_user_id,
                params=params,
            )
        except OneCServiceError as exc:
            if self._can_use_mock(exc):
                logger.warning("1C unavailable in get_products, using mock mode: reason=%s", str(exc))
                return self._mock_products(price_type_id, parent_id)
            raise

        rows = self._extract_collection(
            response,
            ("products", "items", "data", "result", "rows", "list", "goods", "nomenclature"),
        )
        if not rows and isinstance(response, dict):
            rows = [response]

        products: list[Product] = []
        seen_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue

            product_id = self._pick_str(
                row,
                ("id", "product_id", "productId", "uuid", "guid", "ref", "code"),
            )
            name = self._pick_str(
                row,
                ("name", "product_name", "productName", "title", "description"),
            )
            if not product_id or not name or product_id in seen_ids:
                continue

            is_folder = self._pick_bool(
                row,
                ("is_folder", "isFolder", "folder", "is_group", "isGroup", "group"),
                default=False,
            )
            unit = self._normalize_unit(self._pick_str(row, ("unit", "uom", "measure", "unit_name")) or "шт")
            price = self._pick_float(row, ("price", "price_per_unit", "unit_price", "cost"), default=0.0)
            is_promotional = self._is_promotional_product_payload(row, price=price, name=name)

            seen_ids.add(product_id)
            products.append(
                Product(
                    id=product_id,
                    name=name,
                    is_folder=is_folder,
                    unit=unit,
                    price=price,
                    is_promotional=is_promotional,
                ),
            )

        products.sort(key=lambda item: (not item.is_folder, item.name.casefold()))
        return products

    async def create_order(self, order_data: dict[str, Any], telegram_user_id: int) -> OrderResponse:
        client_id = str(order_data.get("client_id", "")).strip()
        contract_id = str(order_data.get("contract_id", "")).strip()
        raw_products = order_data.get("products", order_data.get("items", []))

        if not client_id:
            raise OneCServiceError("Не вдалося сформувати замовлення: відсутній client_id.")
        if not contract_id:
            raise OneCServiceError("Не вдалося сформувати замовлення: відсутній contract_id.")
        if not isinstance(raw_products, list):
            raise OneCServiceError("Не вдалося сформувати замовлення: список товарів некоректний.")

        products_payload: list[dict[str, Any]] = []
        for item in raw_products:
            if not isinstance(item, dict):
                continue
            product_id = str(
                item.get("id")
                or item.get("product_id")
                or item.get("product_uuid")
                or "",
            ).strip()
            quantity = self._normalize_quantity(item.get("quantity", 0))
            price = self._pick_float(item, ("price", "price_per_unit", "unit_price"), default=0.0)

            if not product_id:
                continue
            if isinstance(quantity, (int, float)) and quantity <= 0:
                continue

            products_payload.append(
                {
                    "id": product_id,
                    "quantity": quantity,
                    "price": price,
                },
            )

        if not products_payload:
            raise OneCServiceError("Не вдалося сформувати замовлення: кошик порожній.")

        payload = {
            "client_id": client_id,
            "contract_id": contract_id,
            "products": products_payload,
        }

        if self.mock_mode:
            logger.info("Mock mode enabled: create_order user_id=%s payload=%s", telegram_user_id, payload)
            return OrderResponse(status="success", order_number=f"DEMO-{datetime.now():%Y%m%d%H%M%S}")

        try:
            response = await self._request_json(
                "POST",
                "orders",
                telegram_user_id=telegram_user_id,
                payload=payload,
            )
        except OneCServiceError as exc:
            if self._can_use_mock(exc):
                logger.warning("1C unavailable in create_order, using mock mode: reason=%s", str(exc))
                return OrderResponse(status="success", order_number=f"DEMO-{datetime.now():%Y%m%d%H%M%S}")
            raise

        order_number = self._extract_order_number(response)
        is_success = self._parse_success_response(response)
        if not order_number and not is_success:
            raise OneCServiceError("Не вдалося отримати підтвердження створення замовлення від 1С.")
        return OrderResponse(status="success", order_number=order_number or "N/A")

    async def get_orders_history(self, *, client_id: str, telegram_user_id: int) -> list[OrderHistoryItem]:
        if self.mock_mode:
            logger.info("Mock mode enabled: get_orders_history user_id=%s client_id=%s", telegram_user_id, client_id)
            return self._mock_history(client_id)

        try:
            response = await self._request_json(
                "GET",
                "orders",
                telegram_user_id=telegram_user_id,
                params={"client_id": client_id},
            )
        except OneCServiceError as exc:
            if self._can_use_mock(exc):
                logger.warning("1C unavailable in get_orders_history, using mock mode: reason=%s", str(exc))
                return self._mock_history(client_id)
            raise

        rows = self._extract_collection(response, ("orders", "items", "data", "result", "rows", "list"))
        if not rows and isinstance(response, dict):
            rows = [response]

        orders: list[OrderHistoryItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            number = self._pick_str(
                row,
                ("order_number", "orderNumber", "number", "order_id", "id", "code"),
            )
            order_date = self._pick_str(
                row,
                ("date", "created_at", "createdAt", "datetime", "timestamp"),
            ) or "-"
            total = self._pick_float(row, ("total", "sum", "amount"), default=0.0)
            if not number:
                continue
            orders.append(OrderHistoryItem(order_number=number, date=order_date, total=total))
        return orders

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
