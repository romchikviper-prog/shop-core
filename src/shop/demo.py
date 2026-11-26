"""Корінь композиції: збирає домен, адаптери й показує наскрізний сценарій.

Запуск: py -3 -m shop.demo (з PYTHONPATH=src) або py -3 src/shop/demo.py
з кореня репозиторію після налаштування PYTHONPATH.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypeVar

from shop.application.order_service import OrderService
from shop.domain.commands import AddItem, CancelOrder, CreateOrder, PayOrder, PlaceOrder, ShipOrder
from shop.domain.delivery import Address, StoreId
from shop.domain.errors import DomainError, describe_error
from shop.domain.ids import CustomerId, Sku
from shop.domain.money import Currency, Money
from shop.domain.order import order_total, state_name
from shop.domain.pricing import BasePrice, PercentageDiscount, Vat
from shop.domain.result import Err, Ok, Result
from shop.infrastructure.clock import SystemClock
from shop.infrastructure.memory import InMemoryInventory, InMemoryOrderRepository
from shop.infrastructure.payment_adapter import LegacyPayAdapter, SandboxLegacyPayClient

T = TypeVar("T")


def expect_ok(result: Result[T, DomainError]) -> T:
    """Дістає успішне значення або завершує демонстрацію з описом помилки."""
    match result:
        case Ok(value):
            return value
        case Err(error):
            raise SystemExit(f"Помилка: {describe_error(error)}")


def main() -> None:
    clock = SystemClock()
    orders = InMemoryOrderRepository()
    inventory = InMemoryInventory({Sku("PHN-0001"): 3, Sku("LAP-0002"): 2})
    pricing = Vat(PercentageDiscount(BasePrice(Currency.UAH), Decimal("10")), Decimal("0.20"))
    gateway = LegacyPayAdapter(SandboxLegacyPayClient(), clock)
    service = OrderService(orders, inventory, gateway, pricing, clock)

    customer = CustomerId.new()
    created = expect_ok(service.create_order(CreateOrder(customer)))
    order_id = created.order_id
    print(f"Створено замовлення {order_id} для покупця {customer}")

    expect_ok(
        service.add_item(
            AddItem(
                order_id=order_id,
                sku=Sku("PHN-0001"),
                name="Смартфон Galaxy X",
                unit_price=Money(Decimal("19999.00"), Currency.UAH),
                quantity=2,
            )
        )
    )
    expect_ok(
        service.add_item(
            AddItem(
                order_id=order_id,
                sku=Sku("LAP-0002"),
                name="Ноутбук Pro 14",
                unit_price=Money(Decimal("45999.00"), Currency.UAH),
                quantity=1,
            )
        )
    )

    address = Address(city="Київ", street="Хрещатик", building="1", postal_code="01001")
    placed = expect_ok(
        service.place_order(
            PlaceOrder(
                order_id=order_id,
                delivery_code="courier",
                payment_code="card",
                address=address,
            )
        )
    )
    print(f"Оформлено: {placed.total} (знижка 10%, ПДВ 20%, доставка 99.00 UAH)")

    paid = expect_ok(service.pay_order(PayOrder(order_id=order_id, token="ok-card")))
    print(f"Оплачено: транзакція {paid.transaction_id}")

    shipped = expect_ok(
        service.ship_order(ShipOrder(order_id=order_id, tracking="NP-123456789"))
    )
    print(f"Відправлено: {shipped.tracking}")

    current = service.get_order(order_id)
    assert current is not None
    print(f"Поточний стан: {state_name(current)}, сума: {order_total(current)}")

    print("\n--- Сценарій відхиленого платежу ---")
    second = expect_ok(service.create_order(CreateOrder(CustomerId.new())))
    expect_ok(
        service.add_item(
            AddItem(
                order_id=second.order_id,
                sku=Sku("LAP-0002"),
                name="Ноутбук Pro 14",
                unit_price=Money(Decimal("45999.00"), Currency.UAH),
                quantity=1,
            )
        )
    )
    expect_ok(
        service.place_order(
            PlaceOrder(
                order_id=second.order_id,
                delivery_code="pickup",
                payment_code="bank_transfer",
                store=StoreId("KYIV-001"),
            )
        )
    )
    declined = service.pay_order(PayOrder(order_id=second.order_id, token="decline-card"))
    match declined:
        case Ok():
            print("Неочікувано: платіж пройшов")
        case Err(error):
            print(f"Очікувана відмова: {describe_error(error)}")
    state = service.get_order(second.order_id)
    assert state is not None
    print(f"Стан після відмови: {state_name(state)} (замовлення можна оплатити повторно)")

    before = inventory.available(Sku("LAP-0002"))
    expect_ok(
        service.cancel_order(
            CancelOrder(order_id=second.order_id, reason="Клієнт передумав")
        )
    )
    after = inventory.available(Sku("LAP-0002"))
    print(f"Скасовано; залишок LAP-0002: {before} -> {after}")


if __name__ == "__main__":
    main()
