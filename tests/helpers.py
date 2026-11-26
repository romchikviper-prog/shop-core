"""Спільні фабрики тестових об'єктів."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from shop.domain.delivery import Address, CourierDelivery, DeliveryMethod, TrackingNumber
from shop.domain.errors import describe_error
from shop.domain.ids import CustomerId, OrderId, Sku
from shop.domain.money import Currency, Money
from shop.domain.order import Draft, OrderLine, Paid, Placed, Shipped
from shop.domain.payment import CardPayment, PaymentMethod, PaymentReceipt
from shop.domain.pricing import BasePrice
from shop.domain.result import Err, Ok

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
PHONE = Sku("PHN-0001")
LAPTOP = Sku("LAP-0002")


def uah(amount: str) -> Money:
    return Money(Decimal(amount), Currency.UAH)


def address() -> Address:
    return Address(city="Київ", street="Хрещатик", building="1", postal_code="01001")


def phone_line(quantity: int = 1) -> OrderLine:
    return OrderLine(
        sku=Sku("PHN-0001"),
        name="Смартфон Galaxy X",
        unit_price=uah("19999.00"),
        quantity=quantity,
    )


def laptop_line(quantity: int = 1) -> OrderLine:
    return OrderLine(
        sku=Sku("LAP-0002"),
        name="Ноутбук Pro 14",
        unit_price=uah("45999.00"),
        quantity=quantity,
    )


def make_draft(
    *,
    order_id: OrderId | None = None,
    customer_id: CustomerId | None = None,
    lines: tuple[OrderLine, ...] | None = None,
) -> Draft:
    return Draft(
        order_id=order_id or OrderId.new(),
        customer_id=customer_id or CustomerId.new(),
        lines=lines if lines is not None else (phone_line(),),
        created_at=NOW,
    )


def default_delivery() -> DeliveryMethod:
    return CourierDelivery(address(), uah("99.00"))


def make_placed(
    draft: Draft | None = None,
    *,
    delivery: DeliveryMethod | None = None,
    payment: PaymentMethod | None = None,
    total: Money | None = None,
) -> Placed:
    draft = draft or make_draft()
    delivery = delivery or default_delivery()
    payment = payment or CardPayment()
    if total is None:
        total = BasePrice(Currency.UAH).total(draft.lines).plus(delivery.price)
    result = draft.place(total, delivery, payment, NOW + timedelta(minutes=1))
    match result:
        case Ok(transition):
            return transition.state
        case Err(error):
            raise AssertionError(f"не вдалося оформити тестове замовлення: {describe_error(error)}")


def make_paid(placed: Placed | None = None) -> Paid:
    placed = placed or make_placed()
    receipt = PaymentReceipt("tx-test-0001", placed.total, LATER)
    result = placed.pay(receipt, LATER + timedelta(minutes=1))
    match result:
        case Ok(transition):
            return transition.state
        case Err(error):
            raise AssertionError(f"не вдалося оплатити тестове замовлення: {describe_error(error)}")


def make_shipped(paid: Paid | None = None) -> Shipped:
    paid = paid or make_paid()
    result = paid.ship(TrackingNumber("NP-123456789"), LATER + timedelta(minutes=2))
    match result:
        case Ok(transition):
            return transition.state
        case Err(error):
            raise AssertionError(f"не вдалося відправити тестове замовлення: {describe_error(error)}")
