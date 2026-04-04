class QuantityValidationError(ValueError):
    pass


def validate_quantity(raw_value: str, unit: str) -> int | float:
    value = raw_value.strip().replace(",", ".")

    if unit == "кг":
        try:
            quantity = float(value)
        except ValueError as exc:
            raise QuantityValidationError("Для товару в кг введіть число, наприклад 1.5") from exc

        if quantity <= 0:
            raise QuantityValidationError("Кількість має бути більшою за 0")
        return quantity

    if unit == "шт":
        if not value.isdigit():
            raise QuantityValidationError("Для товару в шт введіть ціле число, наприклад 3")

        quantity = int(value)
        if quantity <= 0:
            raise QuantityValidationError("Кількість має бути більшою за 0")
        return quantity

    raise QuantityValidationError("Невідома одиниця виміру товару")
