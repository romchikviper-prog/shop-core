"""Інваріанти складу: резервування, звільнення, відсутність перепродажу."""

from __future__ import annotations

import pytest

from shop.domain.errors import InsufficientStock, InvalidValue
from shop.domain.ids import OrderId, Sku
from shop.domain.inventory import StockItem, StockRequest
from shop.domain.result import Err, Ok
from shop.infrastructure.memory import InMemoryInventory

PHONE = Sku("PHN-0001")
LAPTOP = Sku("LAP-0002")


def test_stock_item_reserve_moves_units_to_reserved() -> None:
    item = StockItem(PHONE, available=5)
    result = item.reserve(2)
    assert isinstance(result, Ok)
    assert result.value.available == 3
    assert result.value.reserved == 2
    assert item.available == 5


def test_stock_item_reserve_rejects_insufficient_quantity() -> None:
    item = StockItem(PHONE, available=1)
    result = item.reserve(2)
    assert isinstance(result, Err)
    assert isinstance(result.error, InsufficientStock)
    assert result.error.requested == 2
    assert result.error.available == 1


def test_stock_item_release_returns_units_to_available() -> None:
    item = StockItem(PHONE, available=3, reserved=2)
    result = item.release(1)
    assert isinstance(result, Ok)
    assert result.value.available == 4
    assert result.value.reserved == 1


def test_stock_item_release_rejects_more_than_reserved() -> None:
    item = StockItem(PHONE, available=3, reserved=1)
    result = item.release(2)
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)


def test_inventory_reserves_all_or_nothing() -> None:
    inventory = InMemoryInventory({PHONE: 5, LAPTOP: 1})
    order_id = OrderId.new()
    result = inventory.reserve(
        order_id,
        (StockRequest(PHONE, 2), StockRequest(LAPTOP, 2)),
    )
    assert isinstance(result, Err)
    assert inventory.available(PHONE) == 5
    assert inventory.available(LAPTOP) == 1


def test_inventory_reserve_is_idempotent_for_same_order() -> None:
    inventory = InMemoryInventory({PHONE: 5})
    order_id = OrderId.new()
    requests = (StockRequest(PHONE, 2),)
    assert isinstance(inventory.reserve(order_id, requests), Ok)
    assert isinstance(inventory.reserve(order_id, requests), Ok)
    assert inventory.available(PHONE) == 3


def test_inventory_rejects_different_reservation_for_same_order() -> None:
    inventory = InMemoryInventory({PHONE: 5})
    order_id = OrderId.new()
    assert isinstance(inventory.reserve(order_id, (StockRequest(PHONE, 2),)), Ok)
    result = inventory.reserve(order_id, (StockRequest(PHONE, 3),))
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)


def test_inventory_release_restores_stock_and_is_idempotent() -> None:
    inventory = InMemoryInventory({PHONE: 5})
    order_id = OrderId.new()
    assert isinstance(inventory.reserve(order_id, (StockRequest(PHONE, 2),)), Ok)
    inventory.release(order_id)
    assert inventory.available(PHONE) == 5
    inventory.release(order_id)
    assert inventory.available(PHONE) == 5


def test_inventory_unknown_sku_is_out_of_stock() -> None:
    inventory = InMemoryInventory({PHONE: 5})
    result = inventory.reserve(OrderId.new(), (StockRequest(LAPTOP, 1),))
    assert isinstance(result, Err)
    assert isinstance(result.error, InsufficientStock)
    assert result.error.available == 0


@pytest.mark.parametrize("quantity", [0, -1])
def test_stock_request_rejects_non_positive_quantity(quantity: int) -> None:
    with pytest.raises(ValueError):
        StockRequest(PHONE, quantity)
