"""Інваріанти Value Object «Money»."""

from __future__ import annotations

from decimal import Decimal

import pytest

from shop.domain.errors import InvalidValue
from shop.domain.money import Currency, Money
from shop.domain.result import Err, Ok
from tests.helpers import uah


def test_money_rejects_negative_amount() -> None:
    with pytest.raises(ValueError):
        Money(Decimal("-0.01"), Currency.UAH)


def test_money_rejects_non_finite_amount() -> None:
    with pytest.raises(ValueError):
        Money(Decimal("NaN"), Currency.UAH)
    with pytest.raises(ValueError):
        Money(Decimal("Infinity"), Currency.UAH)


def test_money_rounds_half_up_to_cents() -> None:
    assert Money(Decimal("10.005"), Currency.UAH).amount == Decimal("10.01")
    assert Money(Decimal("10.004"), Currency.UAH).amount == Decimal("10.00")


def test_money_adds_same_currency() -> None:
    assert uah("10.10").plus(uah("0.90")) == uah("11.00")


def test_money_rejects_currency_mismatch() -> None:
    with pytest.raises(ValueError):
        uah("10.00").plus(Money(Decimal("10.00"), Currency.USD))


def test_money_scales_and_multiplies() -> None:
    assert uah("100.00").scale(Decimal("0.90")) == uah("90.00")
    assert uah("19.99").times(3) == uah("59.97")
    assert uah("100.00").scale(Decimal("0")) == uah("0.00")


def test_money_times_rejects_zero_quantity() -> None:
    with pytest.raises(ValueError):
        uah("10.00").times(0)


def test_money_string_uses_iso_code_and_dot() -> None:
    assert str(uah("19999.5")) == "19999.50 UAH"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("19999", "19999.00"),
        ("19999.5", "19999.50"),
        ("19999,50", "19999.50"),
        ("0", "0.00"),
        (" 42.10 ", "42.10"),
    ],
)
def test_money_parse_accepts_valid_values(raw: str, expected: str) -> None:
    result = Money.parse(raw, Currency.UAH)
    assert isinstance(result, Ok)
    assert result.value == uah(expected)


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "-1", "abc", "NaN", "Infinity", "10.999", "1 000"],
)
def test_money_parse_returns_error_for_invalid_values(raw: str) -> None:
    result = Money.parse(raw, Currency.UAH)
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)
    assert result.error.field == "amount"
