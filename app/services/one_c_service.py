import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Product:
    name: str
    unit: str
    category: str


class OneCService:
    def __init__(self) -> None:
        self._clients = [
            "ФОП Петренко",
            "ТОВ Смак М'яса",
        ]
        self._catalog: dict[str, list[Product]] = {
            "Ковбаси": [
                Product(name="Ковбаса Докторська (кг)", unit="кг", category="Ковбаси"),
            ],
            "Паштети": [
                Product(name="Паштет (шт)", unit="шт", category="Паштети"),
            ],
        }

    def check_auth(self, phone: str, code: str) -> bool:
        masked_phone = f"***{phone[-4:]}" if len(phone) >= 4 else "***"
        logger.info("1C auth check started for phone=%s", masked_phone)

        is_authorized = code == "1234"
        if is_authorized:
            logger.info("1C auth check success for phone=%s", masked_phone)
        else:
            logger.warning("1C auth check failed for phone=%s", masked_phone)

        return is_authorized

    def get_clients(self) -> list[str]:
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
