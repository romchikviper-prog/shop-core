"""Прикладна логіка HTTP-сервісу: розбір запиту й виклик доменного модуля.

Сервіс навмисно тонкий: він не дублює правила ціноутворення, а викликає
`calculate_order_price` з ЛР4. Межа відповідальності — перевірка JSON і
перетворення `Decimal` у рядки, щоб клієнт не втрачав копійки через
плаваючу кому.

Формат запиту:

    {
      "items": [{"name": "Телефон", "price": "100.00", "quantity": 10}],
      "customer_type": "gold",
      "loyalty_years": 0,
      "promo_code": "SAVE20",
      "delivery": "pickup"
    }

Усі поля, крім `items`, необов'язкові. Невідомі значення `customer_type`
та `delivery` обробляються спадковим модулем як `regular` і `pickup`.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from shop.legacy.order_pricing import LegacyItem, calculate_order_price

MONEY_FIELDS = (
    "subtotal",
    "quantity_discount",
    "coupon_discount",
    "loyalty_discount",
    "tax",
    "delivery",
    "total",
)


class RequestError(ValueError):
    """Клієнт надіслав некоректний запит; відповідь буде 400."""


def evaluate(payload: object) -> dict[str, str | None]:
    """Розраховує ціну замовлення за JSON-сумісним payload."""

    data = _mapping(payload, "тіло запиту")
    items = _items(data.get("items"))
    customer_type = _text(data.get("customer_type"), "customer_type", "regular")
    loyalty_years = _integer(data.get("loyalty_years"), "loyalty_years", 0)
    promo_code = _optional_text(data.get("promo_code"), "promo_code")
    delivery = _text(data.get("delivery"), "delivery", "pickup")

    report = calculate_order_price(
        items,
        customer_type,
        loyalty_years,
        promo_code,
        delivery,
    )
    return _serialize(report)


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RequestError(f"{field} має бути JSON-об'єктом")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise RequestError(f"{field} має містити лише рядкові ключі")
        result[key] = item
    return result


def _items(value: object) -> list[LegacyItem]:
    if not isinstance(value, list) or not value:
        raise RequestError("items має бути непорожнім масивом")
    return [_item(item, index) for index, item in enumerate(value)]


def _item(value: object, index: int) -> LegacyItem:
    data = _mapping(value, f"items[{index}]")
    return LegacyItem(
        name=_required_text(data.get("name"), f"items[{index}].name"),
        price=_price(data.get("price"), f"items[{index}].price"),
        quantity=_integer(data.get("quantity"), f"items[{index}].quantity"),
    )


def _price(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise RequestError(f'{field} має бути рядком, наприклад "199.99"')
    try:
        price = Decimal(value.strip())
    except InvalidOperation:
        raise RequestError(f"{field} має бути десятковим числом") from None
    if not price.is_finite():
        raise RequestError(f"{field} має бути скінченним числом")
    if price < 0:
        raise RequestError(f"{field} не може бути від'ємною")
    return price


def _integer(value: object, field: str, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise RequestError(f"{field} є обов'язковим")
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestError(f"{field} має бути цілим числом")
    if value < 0:
        raise RequestError(f"{field} не може бути від'ємним")
    return value


def _text(value: object, field: str, default: str) -> str:
    if value is None:
        return default
    return _required_text(value, field)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequestError(f"{field} має бути непорожнім рядком")
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _serialize(report: dict[str, object]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for field in MONEY_FIELDS:
        result[field] = _money_text(report[field], field)

    promo_code = report["promo_code"]
    if promo_code is not None and not isinstance(promo_code, str):
        raise RuntimeError("calculate_order_price повернув некоректний promo_code")
    result["promo_code"] = promo_code

    customer_type = report["customer_type"]
    if not isinstance(customer_type, str):
        raise RuntimeError("calculate_order_price повернув некоректний customer_type")
    result["customer_type"] = customer_type
    return result


def _money_text(value: object, field: str) -> str:
    if not isinstance(value, Decimal):
        raise RuntimeError(f"calculate_order_price повернув некоректне поле {field}")
    return f"{value:.2f}"
