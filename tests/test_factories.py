"""Фабрики способів доставки й оплати."""

from __future__ import annotations

import pytest

from shop.domain.delivery import CourierDelivery, ExpressDelivery, PickupDelivery, StoreId
from shop.domain.errors import InvalidValue, UnknownDeliveryMethod, UnknownPaymentMethod
from shop.domain.factories import (
    COURIER_PRICE,
    EXPRESS_PRICE,
    CourierDeliveryFactory,
    ExpressDeliveryFactory,
    PickupDeliveryFactory,
    make_delivery,
    make_payment,
)
from shop.domain.payment import BankTransferPayment, CardPayment
from shop.domain.result import Err, Ok
from tests.helpers import address, uah


def test_courier_factory_creates_courier_delivery() -> None:
    method = CourierDeliveryFactory(address()).create()
    assert isinstance(method, CourierDelivery)
    assert method.price == COURIER_PRICE == uah("99.00")
    assert str(method.address) == "Київ, Хрещатик, 1, 01001"


def test_pickup_factory_creates_free_pickup() -> None:
    method = PickupDeliveryFactory(StoreId("KYIV-001")).create()
    assert isinstance(method, PickupDelivery)
    assert method.price == uah("0.00")
    assert str(method.store) == "KYIV-001"


def test_express_factory_creates_express_delivery() -> None:
    method = ExpressDeliveryFactory(address()).create()
    assert isinstance(method, ExpressDelivery)
    assert method.price == EXPRESS_PRICE == uah("199.00")


def test_make_delivery_courier_by_code() -> None:
    result = make_delivery("courier", address=address())
    assert isinstance(result, Ok)
    assert isinstance(result.value, CourierDelivery)


def test_make_delivery_requires_address_for_courier() -> None:
    result = make_delivery("courier")
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)
    assert result.error.field == "address"


def test_make_delivery_requires_store_for_pickup() -> None:
    result = make_delivery("pickup")
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)
    assert result.error.field == "store"


@pytest.mark.parametrize("code", ["drone", "", "  "])
def test_make_delivery_rejects_unknown_code(code: str) -> None:
    result = make_delivery(code, address=address())
    assert isinstance(result, Err)
    assert isinstance(result.error, UnknownDeliveryMethod)


@pytest.mark.parametrize(
    ("code", "expected_type"),
    [("card", CardPayment), ("bank_transfer", BankTransferPayment)],
)
def test_make_payment_by_code(code: str, expected_type: type[object]) -> None:
    result = make_payment(code)
    assert isinstance(result, Ok)
    assert isinstance(result.value, expected_type)


def test_make_payment_rejects_unknown_code() -> None:
    result = make_payment("crypto")
    assert isinstance(result, Err)
    assert isinstance(result.error, UnknownPaymentMethod)
