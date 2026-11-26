"""Доменні події замовлення як закрита множина варіантів.

Подія описує те, що вже відбулося, у минулому часі. Події повертають
переходи станів агрегату, тому вони завжди узгоджені з новим станом.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias

from .ids import CustomerId, OrderId
from .money import Money


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("час події має бути з часовою зоною")


@dataclass(frozen=True, slots=True)
class OrderCreated:
    """Створено чернетку замовлення."""

    order_id: OrderId
    customer_id: CustomerId
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class OrderPlaced:
    """Замовлення оформлено, товар зарезервовано."""

    order_id: OrderId
    total: Money
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class OrderPaid:
    """Платіж успішно підтверджено."""

    order_id: OrderId
    total: Money
    transaction_id: str
    at: datetime

    def __post_init__(self) -> None:
        if not self.transaction_id.strip():
            raise ValueError("transaction_id не може бути порожнім")
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class OrderShipped:
    """Замовлення передано службі доставки."""

    order_id: OrderId
    tracking: str
    at: datetime

    def __post_init__(self) -> None:
        if not self.tracking.strip():
            raise ValueError("tracking не може бути порожнім")
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class OrderCancelled:
    """Замовлення скасовано."""

    order_id: OrderId
    reason: str
    at: datetime

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("reason не може бути порожнім")
        _require_aware(self.at)


OrderEvent: TypeAlias = OrderCreated | OrderPlaced | OrderPaid | OrderShipped | OrderCancelled
