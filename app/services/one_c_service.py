import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


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
    def __init__(self) -> None:
        self._clients = [
            Client(id="client_1", name="ФОП Петренко"),
            Client(id="client_2", name="ТОВ Смак М'яса"),
        ]
        self._catalog: dict[str, list[Product]] = {
            "Ковбаси": [
                Product(
                    name="Ковбаса Докторська (кг)",
                    unit="кг",
                    category="Ковбаси",
                    price_per_unit=285.0,
                ),
            ],
            "Паштети": [
                Product(name="Паштет (шт)", unit="шт", category="Паштети", price_per_unit=42.0),
            ],
        }
        self._trading_points: dict[str, list[str]] = {
            "client_1": ["Магазин Петренко №1", "Кіоск Петренко Центр"],
            "client_2": ["Смак М'яса Склад", "Смак М'яса Ринок"],
        }
        self._order_counter = 1

    def check_auth(self, phone: str, code: str) -> bool:
        masked_phone = f"***{phone[-4:]}" if len(phone) >= 4 else "***"
        logger.info("1C auth check started for phone=%s", masked_phone)

        is_authorized = code == "1234"
        if is_authorized:
            logger.info("1C auth check success for phone=%s", masked_phone)
        else:
            logger.warning("1C auth check failed for phone=%s", masked_phone)

        return is_authorized

    def get_clients(self) -> list[Client]:
        logger.info("Fetching clients from 1C mock")
        return list(self._clients)

    def get_categories(self) -> list[str]:
        logger.info("Fetching product categories from 1C mock")
        return list(self._catalog.keys())

    def get_products(self, category: str) -> list[Product]:
        logger.info("Fetching products from 1C mock for category=%s", category)
        return list(self._catalog.get(category, []))

    def find_product(self, category: str, product_name: str) -> Product | None:
        products = self.get_products(category)
        for product in products:
            if product.name == product_name:
                return product
        return None

    def get_trading_points(self, client_id: str) -> list[str]:
        logger.info("Fetching trading points from 1C mock for client_id=%s", client_id)
        return list(self._trading_points.get(client_id, []))

    def create_order(self, order_data: dict[str, Any]) -> dict[str, Any]:
        logger.info("Simulating POST to 1C with order payload: %s", order_data)
        order_number = f"ORD-{datetime.now().strftime('%Y%m%d')}-{self._order_counter:04d}"
        self._order_counter += 1
        logger.info("1C mock order created successfully: order_number=%s", order_number)
        return {
            "status": "success",
            "order_number": order_number,
        }
