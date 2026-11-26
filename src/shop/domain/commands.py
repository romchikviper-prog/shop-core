"""Команди, які приймає сервіс замовлень.

Команда — це намір змінити стан. Команди є закритою множиною варіантів,
тому диспетчер команд перевіряє вичерпність обробки під час перевірки типів.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .delivery import Address, StoreId
from .ids import CustomerId, OrderId, Sku
from .money import Money


@dataclass(frozen=True, slots=True)
class CreateOrder:
    """Створити чернетку замовлення для покупця."""

    customer_id: CustomerId


@dataclass(frozen=True, slots=True)
class AddItem:
    """Додати товар у кошик."""

    order_id: OrderId
    sku: Sku
    name: str
    unit_price: Money
    quantity: int


@dataclass(frozen=True, slots=True)
class RemoveItem:
    """Прибрати товар із кошика."""

    order_id: OrderId
    sku: Sku


@dataclass(frozen=True, slots=True)
class PlaceOrder:
    """Оформити замовлення з вибраними доставкою та оплатою."""

    order_id: OrderId
    delivery_code: str
    payment_code: str
    address: Address | None = None
    store: StoreId | None = None


@dataclass(frozen=True, slots=True)
class PayOrder:
    """Оплатити оформлене замовлення."""

    order_id: OrderId
    token: str


@dataclass(frozen=True, slots=True)
class ShipOrder:
    """Передати оплачене замовлення в доставку."""

    order_id: OrderId
    tracking: str


@dataclass(frozen=True, slots=True)
class CancelOrder:
    """Скасувати замовлення, яке ще не оплачене."""

    order_id: OrderId
    reason: str


Command: TypeAlias = CreateOrder | AddItem | RemoveItem | PlaceOrder | PayOrder | ShipOrder | CancelOrder
