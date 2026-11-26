"""Інваріанти ціноутворення та композиції декораторів."""

from __future__ import annotations

from decimal import Decimal

import pytest

from shop.domain.money import Currency, Money
from shop.domain.order import OrderLine
from shop.domain.pricing import BasePrice, FixedDiscount, PercentageDiscount, Vat
from tests.helpers import laptop_line, phone_line, uah


def test_base_price_sums_lines() -> None:
    total = BasePrice(Currency.UAH).total((phone_line(2), laptop_line()))
    assert total == uah("85997.00")


def test_base_price_of_empty_cart_is_zero() -> None:
    assert BasePrice(Currency.UAH).total(()) == uah("0.00")


@pytest.mark.parametrize(
    ("percent", "expected"),
    [("0", "85997.00"), ("10", "77397.30"), ("100", "0.00")],
)
def test_percentage_discount(percent: str, expected: str) -> None:
    calculator = PercentageDiscount(BasePrice(Currency.UAH), Decimal(percent))
    assert calculator.total((phone_line(2), laptop_line())) == uah(expected)


@pytest.mark.parametrize(
    ("discount", "expected"),
    [("100.00", "85897.00"), ("1000.00", "84997.00"), ("100000.00", "0.00")],
)
def test_fixed_discount_never_goes_negative(discount: str, expected: str) -> None:
    calculator = FixedDiscount(BasePrice(Currency.UAH), uah(discount))
    assert calculator.total((phone_line(2), laptop_line())) == uah(expected)


def test_vat_adds_tax() -> None:
    calculator = Vat(BasePrice(Currency.UAH), Decimal("0.20"))
    assert calculator.total((phone_line(2), laptop_line())) == uah("103196.40")


def test_fixed_discount_and_vat_order_changes_result() -> None:
    lines = (phone_line(2), laptop_line())
    discount_then_vat = Vat(
        FixedDiscount(BasePrice(Currency.UAH), uah("1000.00")),
        Decimal("0.20"),
    ).total(lines)
    vat_then_discount = FixedDiscount(
        Vat(BasePrice(Currency.UAH), Decimal("0.20")),
        uah("1000.00"),
    ).total(lines)
    assert discount_then_vat == uah("101996.40")
    assert vat_then_discount == uah("102196.40")
    assert discount_then_vat != vat_then_discount


@pytest.mark.parametrize("percent", ["-1", "101", "NaN"])
def test_percentage_discount_rejects_invalid_percent(percent: str) -> None:
    with pytest.raises(ValueError):
        PercentageDiscount(BasePrice(Currency.UAH), Decimal(percent))


@pytest.mark.parametrize("rate", ["-0.1", "1.1", "NaN"])
def test_vat_rejects_invalid_rate(rate: str) -> None:
    with pytest.raises(ValueError):
        Vat(BasePrice(Currency.UAH), Decimal(rate))


def test_fixed_discount_rejects_currency_mismatch() -> None:
    with pytest.raises(ValueError):
        FixedDiscount(
            BasePrice(Currency.UAH),
            Money(Decimal("10.00"), Currency.USD),
        ).total((phone_line(),))


def test_base_price_rejects_line_in_other_currency() -> None:
    foreign_line = OrderLine(
        sku=laptop_line().sku,
        name=laptop_line().name,
        unit_price=Money(Decimal("100.00"), Currency.USD),
        quantity=1,
    )
    with pytest.raises(ValueError):
        BasePrice(Currency.UAH).total((foreign_line,))
