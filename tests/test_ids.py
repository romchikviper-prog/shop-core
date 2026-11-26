"""Інваріанти ідентифікаторів, артикулів і адрес."""

from __future__ import annotations

import pytest

from shop.domain.delivery import Address, StoreId, TrackingNumber
from shop.domain.errors import InvalidValue
from shop.domain.ids import CustomerId, OrderId, Sku
from shop.domain.result import Err, Ok


@pytest.mark.parametrize("raw", ["PHN-0001", "LAP-0002", "HDP-9999"])
def test_sku_accepts_valid_format(raw: str) -> None:
    sku = Sku(raw)
    assert str(sku) == raw
    result = Sku.parse(raw)
    assert isinstance(result, Ok)
    assert result.value == sku


@pytest.mark.parametrize(
    "raw",
    ["", "phn-0001", "PHONE-0001", "PHN-1", "PHN-00001", "PHN_0001"],
)
def test_sku_rejects_invalid_format(raw: str) -> None:
    with pytest.raises(ValueError):
        Sku(raw)
    result = Sku.parse(raw)
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)


def test_sku_parse_trims_external_whitespace() -> None:
    result = Sku.parse(" PHN-0001 ")
    assert isinstance(result, Ok)
    assert result.value == Sku("PHN-0001")


def test_order_id_round_trip_and_uniqueness() -> None:
    first = OrderId.new()
    second = OrderId.new()
    assert first != second
    parsed = OrderId.parse(str(first))
    assert isinstance(parsed, Ok)
    assert parsed.value == first


def test_order_id_parse_error() -> None:
    result = OrderId.parse("not-a-uuid")
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)
    assert result.error.field == "order_id"


def test_customer_id_parse_error() -> None:
    result = CustomerId.parse("not-a-uuid")
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)
    assert result.error.field == "customer_id"


def test_tracking_number_normalizes_case() -> None:
    result = TrackingNumber.parse(" np-123456789 ")
    assert isinstance(result, Ok)
    assert result.value.value == "NP-123456789"


@pytest.mark.parametrize("raw", ["", "AB", "NP 123", "NP_123456", "!@#$%^&"])
def test_tracking_number_rejects_invalid(raw: str) -> None:
    result = TrackingNumber.parse(raw)
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)


def test_address_rejects_invalid_postal_code() -> None:
    with pytest.raises(ValueError):
        Address(city="Київ", street="Хрещатик", building="1", postal_code="1234")


def test_address_rejects_empty_city() -> None:
    with pytest.raises(ValueError):
        Address(city=" ", street="Хрещатик", building="1", postal_code="01001")


def test_store_id_requires_format() -> None:
    assert str(StoreId("KYIV-001")) == "KYIV-001"
    with pytest.raises(ValueError):
        StoreId("kyiv-1")
