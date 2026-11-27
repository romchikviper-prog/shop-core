"""Сценарії сервісу замовлень і взаємодія з портами."""

from __future__ import annotations

import pytest

from shop.application.order_service import OrderService
from shop.domain.commands import AddItem, CancelOrder, CreateOrder, PayOrder, PlaceOrder, RemoveItem, ShipOrder
from shop.domain.delivery import StoreId
from shop.domain.errors import (
    EmptyOrder,
    InsufficientStock,
    InvalidState,
    InvalidValue,
    PaymentDeclined,
    UnknownDeliveryMethod,
)
from shop.domain.events import OrderCreated, OrderPlaced
from shop.domain.ids import CustomerId, OrderId
from shop.domain.money import Currency
from shop.domain.order import Cancelled, Draft, Paid, Placed, Shipped
from shop.domain.payment import PaymentReceipt
from shop.domain.pricing import BasePrice
from shop.domain.result import Err, Ok, Result
from shop.infrastructure.memory import InMemoryInventory, InMemoryOrderRepository
from tests.fakes import ApprovingGateway, FixedClock, SequenceGateway
from tests.helpers import NOW, PHONE, address, uah


def create_order(service: OrderService) -> OrderId:
    result = service.create_order(CreateOrder(CustomerId.new()))
    assert isinstance(result, Ok)
    return result.value.order_id


def add_phone(service: OrderService, order_id: OrderId, quantity: int = 1) -> None:
    result = service.add_item(
        AddItem(
            order_id=order_id,
            sku=PHONE,
            name="Смартфон Galaxy X",
            unit_price=uah("19999.00"),
            quantity=quantity,
        )
    )
    assert isinstance(result, Ok)


def place_order(
    service: OrderService,
    order_id: OrderId,
    *,
    delivery_code: str = "courier",
    payment_code: str = "card",
) -> Result[OrderPlaced, object]:
    return service.place_order(
        PlaceOrder(
            order_id=order_id,
            delivery_code=delivery_code,
            payment_code=payment_code,
            address=address(),
            store=StoreId("KYIV-001"),
        )
    )


def test_create_order_saves_draft(service: OrderService, orders: InMemoryOrderRepository) -> None:
    customer = CustomerId.new()
    result = service.create_order(CreateOrder(customer))
    assert isinstance(result, Ok)
    created = result.value
    assert isinstance(created, OrderCreated)
    stored = orders.get(created.order_id)
    assert isinstance(stored, Draft)
    assert stored.customer_id == customer
    assert stored.lines == ()


def test_add_item_updates_draft(service: OrderService, orders: InMemoryOrderRepository) -> None:
    order_id = create_order(service)
    result = service.add_item(
        AddItem(order_id, PHONE, "Смартфон Galaxy X", uah("19999.00"), 2)
    )
    assert isinstance(result, Ok)
    stored = orders.get(order_id)
    assert isinstance(stored, Draft)
    assert len(stored.lines) == 1
    assert stored.lines[0].quantity == 2


def test_remove_item_updates_draft(service: OrderService) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    result = service.remove_item(RemoveItem(order_id, PHONE))
    assert isinstance(result, Ok)
    order = service.get_order(order_id)
    assert isinstance(order, Draft)
    assert order.lines == ()


def test_place_order_reserves_stock_and_saves_placed(
    service: OrderService,
    inventory: InMemoryInventory,
) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    result = place_order(service, order_id)
    assert isinstance(result, Ok)
    assert result.value.total == uah("20098.00")
    order = service.get_order(order_id)
    assert isinstance(order, Placed)
    assert inventory.available(PHONE) == 4


def test_place_order_with_pickup_has_no_delivery_cost(service: OrderService) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    result = place_order(service, order_id, delivery_code="pickup")
    assert isinstance(result, Ok)
    assert result.value.total == uah("19999.00")


def test_place_order_with_insufficient_stock_keeps_draft(
    service: OrderService,
    inventory: InMemoryInventory,
) -> None:
    empty_inventory = InMemoryInventory({PHONE: 0})
    local_service = OrderService(
        orders=InMemoryOrderRepository(),
        inventory=empty_inventory,
        payments=ApprovingGateway(),
        pricing=BasePrice(Currency.UAH),
        clock=FixedClock(),
    )
    order_id = create_order(local_service)
    add_phone(local_service, order_id)
    result = place_order(local_service, order_id)
    assert isinstance(result, Err)
    assert isinstance(result.error, InsufficientStock)
    order = local_service.get_order(order_id)
    assert isinstance(order, Draft)
    assert empty_inventory.available(PHONE) == 0


def test_place_order_with_empty_cart_returns_error(
    service: OrderService,
    inventory: InMemoryInventory,
) -> None:
    order_id = create_order(service)
    result = place_order(service, order_id)
    assert isinstance(result, Err)
    assert isinstance(result.error, EmptyOrder)
    order = service.get_order(order_id)
    assert isinstance(order, Draft)
    assert inventory.available(PHONE) == 5


def test_place_order_with_unknown_delivery_keeps_stock(
    service: OrderService,
    inventory: InMemoryInventory,
) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    result = place_order(service, order_id, delivery_code="drone")
    assert isinstance(result, Err)
    assert isinstance(result.error, UnknownDeliveryMethod)
    assert inventory.available(PHONE) == 5


def test_pay_order_charges_and_moves_to_paid(
    service: OrderService,
    gateway: ApprovingGateway,
) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    placed = place_order(service, order_id)
    assert isinstance(placed, Ok)

    result = service.pay_order(PayOrder(order_id, "ok-card"))
    assert isinstance(result, Ok)
    order = service.get_order(order_id)
    assert isinstance(order, Paid)
    assert len(gateway.requests) == 1
    assert gateway.requests[0].amount == placed.value.total
    assert gateway.requests[0].idempotency_key == f"order:{order_id}:payment"
    assert gateway.requests[0].token == "ok-card"


def test_pay_order_twice_does_not_charge_twice(
    service: OrderService,
    gateway: ApprovingGateway,
) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    assert isinstance(place_order(service, order_id), Ok)
    assert isinstance(service.pay_order(PayOrder(order_id, "ok-card")), Ok)

    second = service.pay_order(PayOrder(order_id, "ok-card"))
    assert isinstance(second, Err)
    assert isinstance(second.error, InvalidState)
    assert len(gateway.requests) == 1


def test_declined_payment_keeps_order_placed_and_can_be_retried(
    orders: InMemoryOrderRepository,
    inventory: InMemoryInventory,
) -> None:
    first_service = OrderService(
        orders=orders,
        inventory=inventory,
        payments=ApprovingGateway(),
        pricing=BasePrice(Currency.UAH),
        clock=FixedClock(),
    )
    order_id = create_order(first_service)
    add_phone(first_service, order_id)
    placed = place_order(first_service, order_id)
    assert isinstance(placed, Ok)

    gateway = SequenceGateway(
        [
            Err(PaymentDeclined("недостатньо коштів")),
            Ok(PaymentReceipt("tx-retry", placed.value.total, NOW)),
        ]
    )
    retry_service = OrderService(
        orders=orders,
        inventory=inventory,
        payments=gateway,
        pricing=BasePrice(Currency.UAH),
        clock=FixedClock(),
    )

    declined = retry_service.pay_order(PayOrder(order_id, "card-token"))
    assert isinstance(declined, Err)
    assert isinstance(declined.error, PaymentDeclined)
    assert isinstance(retry_service.get_order(order_id), Placed)

    paid = retry_service.pay_order(PayOrder(order_id, "card-token"))
    assert isinstance(paid, Ok)
    assert isinstance(retry_service.get_order(order_id), Paid)
    assert len(gateway.requests) == 2
    assert gateway.requests[0].idempotency_key == gateway.requests[1].idempotency_key


def test_pay_order_requires_token(service: OrderService, gateway: ApprovingGateway) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    assert isinstance(place_order(service, order_id), Ok)
    result = service.pay_order(PayOrder(order_id, "   "))
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)
    assert gateway.requests == []


def test_ship_order_moves_paid_to_shipped(service: OrderService) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    assert isinstance(place_order(service, order_id), Ok)
    assert isinstance(service.pay_order(PayOrder(order_id, "ok-card")), Ok)

    result = service.ship_order(ShipOrder(order_id, "NP-123456789"))
    assert isinstance(result, Ok)
    order = service.get_order(order_id)
    assert isinstance(order, Shipped)
    assert order.tracking.value == "NP-123456789"


def test_ship_order_before_payment_is_rejected(service: OrderService) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    assert isinstance(place_order(service, order_id), Ok)
    result = service.ship_order(ShipOrder(order_id, "NP-123456789"))
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidState)


def test_cancel_placed_order_releases_stock(
    service: OrderService,
    inventory: InMemoryInventory,
) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    assert isinstance(place_order(service, order_id), Ok)
    assert inventory.available(PHONE) == 4

    result = service.cancel_order(CancelOrder(order_id, "клієнт передумав"))
    assert isinstance(result, Ok)
    order = service.get_order(order_id)
    assert isinstance(order, Cancelled)
    assert inventory.available(PHONE) == 5


def test_cancel_paid_order_is_rejected(service: OrderService, inventory: InMemoryInventory) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    assert isinstance(place_order(service, order_id), Ok)
    assert isinstance(service.pay_order(PayOrder(order_id, "ok-card")), Ok)

    result = service.cancel_order(CancelOrder(order_id, "пізно"))
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidState)
    assert isinstance(service.get_order(order_id), Paid)
    assert inventory.available(PHONE) == 4


def test_cancel_draft_does_not_touch_stock(
    service: OrderService,
    inventory: InMemoryInventory,
) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    result = service.cancel_order(CancelOrder(order_id, "не потрібно"))
    assert isinstance(result, Ok)
    assert isinstance(service.get_order(order_id), Cancelled)
    assert inventory.available(PHONE) == 5


def test_add_item_to_placed_order_is_rejected(service: OrderService) -> None:
    order_id = create_order(service)
    add_phone(service, order_id)
    assert isinstance(place_order(service, order_id), Ok)
    result = service.add_item(
        AddItem(order_id, PHONE, "Смартфон Galaxy X", uah("19999.00"), 1)
    )
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidState)


def test_handle_dispatches_create_order(service: OrderService) -> None:
    result = service.handle(CreateOrder(CustomerId.new()))
    assert isinstance(result, Ok)
    assert isinstance(result.value, OrderCreated)
    assert isinstance(service.get_order(result.value.order_id), Draft)


def test_get_unknown_order_returns_none(service: OrderService) -> None:
    assert service.get_order(OrderId.new()) is None


@pytest.mark.parametrize("quantity", [0, -1])
def test_add_item_with_invalid_quantity_returns_error(service: OrderService, quantity: int) -> None:
    order_id = create_order(service)
    result = service.add_item(
        AddItem(order_id, PHONE, "Смартфон Galaxy X", uah("19999.00"), quantity)
    )
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)
