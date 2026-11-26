"""Очікувані помилки домену як закрита множина варіантів.

Помилки є даними: їх можна повернути з Result, передати в UI,
залогувати або обробити pattern matching без try/except.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias, assert_never

if TYPE_CHECKING:
    from .ids import OrderId, Sku
    from .money import Currency, Money


@dataclass(frozen=True, slots=True)
class InvalidValue:
    """Порушено формат або діапазон значення."""

    field: str
    reason: str


@dataclass(frozen=True, slots=True)
class CurrencyMismatch:
    """Спроба додати суми в різних валютах."""

    left: Currency
    right: Currency


@dataclass(frozen=True, slots=True)
class EmptyOrder:
    """Замовлення без жодної позиції не можна оформити."""

    pass


@dataclass(frozen=True, slots=True)
class OrderNotFound:
    """Замовлення із таким ідентифікатором не існує."""

    order_id: OrderId


@dataclass(frozen=True, slots=True)
class InvalidState:
    """Команда недопустима для поточного стану агрегату."""

    expected: str
    actual: str


@dataclass(frozen=True, slots=True)
class InsufficientStock:
    """На складі недостатньо одиниць товару."""

    sku: Sku
    requested: int
    available: int


@dataclass(frozen=True, slots=True)
class UnknownDeliveryMethod:
    """Невідомий код способу доставки."""

    code: str


@dataclass(frozen=True, slots=True)
class UnknownPaymentMethod:
    """Невідомий код способу оплати."""

    code: str


@dataclass(frozen=True, slots=True)
class TotalMismatch:
    """Сума підтвердженого платежу не дорівнює сумі замовлення."""

    expected: Money
    actual: Money


@dataclass(frozen=True, slots=True)
class PaymentDeclined:
    """Платіжний шлюз відхилив операцію."""

    reason: str


@dataclass(frozen=True, slots=True)
class PaymentUnavailable:
    """Платіжний шлюз недоступний або повернув неочікувану відповідь."""

    reason: str


DomainError: TypeAlias = (
    InvalidValue
    | CurrencyMismatch
    | EmptyOrder
    | OrderNotFound
    | InvalidState
    | InsufficientStock
    | UnknownDeliveryMethod
    | UnknownPaymentMethod
    | TotalMismatch
    | PaymentDeclined
    | PaymentUnavailable
)


def describe_error(error: DomainError) -> str:
    """Людяночитаний опис помилки українською."""
    match error:
        case InvalidValue(field, reason):
            return f"Поле «{field}»: {reason}"
        case CurrencyMismatch(left, right):
            return f"Валюти не збігаються: {left.value} і {right.value}"
        case EmptyOrder():
            return "Замовлення не містить жодного товару"
        case OrderNotFound(order_id):
            return f"Замовлення {order_id} не знайдено"
        case InvalidState(expected, actual):
            return f"Очікувався стан {expected}, фактичний — {actual}"
        case InsufficientStock(sku, requested, available):
            return f"Недостатньо товару {sku}: потрібно {requested}, доступно {available}"
        case UnknownDeliveryMethod(code):
            return f"Невідомий спосіб доставки: {code!r}"
        case UnknownPaymentMethod(code):
            return f"Невідомий спосіб оплати: {code!r}"
        case TotalMismatch(expected, actual):
            return f"Сума платежу {actual} не збігається із сумою замовлення {expected}"
        case PaymentDeclined(reason):
            return f"Платіж відхилено: {reason}"
        case PaymentUnavailable(reason):
            return f"Платіжний шлюз недоступний: {reason}"
        case _:
            assert_never(error)
