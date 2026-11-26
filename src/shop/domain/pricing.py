"""Ціноутворення кошика: базові ціни, знижки та податки.

Патерн Decorator: кожна знижка або податок обгортає попередній калькулятор
і додає одну незалежну трансформацію суми. Порядок обгортання визначає
бізнес-правило (спочатку знижка, потім ПДВ), а сам агрегат замовлення
не знає, скільки знижок існує.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from .money import Currency, Money
from .order import OrderLine


class PriceCalculator(Protocol):
    """Спільний інтерфейс калькулятора вартості позицій."""

    def total(self, lines: Sequence[OrderLine]) -> Money: ...


@dataclass(frozen=True, slots=True)
class BasePrice:
    """Сума добутків ціни на кількість без знижок і податків."""

    currency: Currency = Currency.UAH

    def total(self, lines: Sequence[OrderLine]) -> Money:
        result = Money.zero(self.currency)
        for line in lines:
            if line.unit_price.currency != self.currency:
                raise ValueError(
                    f"позиція {line.sku} у валюті {line.unit_price.currency.value}, "
                    f"а калькулятор — {self.currency.value}"
                )
            result = result.plus(line.total)
        return result


@dataclass(frozen=True, slots=True)
class PercentageDiscount:
    """Відсоткова знижка на суму попереднього калькулятора."""

    inner: PriceCalculator
    percent: Decimal

    def __post_init__(self) -> None:
        if not self.percent.is_finite() or not (Decimal(0) <= self.percent <= Decimal(100)):
            raise ValueError("відсоток знижки має бути від 0 до 100")

    def total(self, lines: Sequence[OrderLine]) -> Money:
        factor = (Decimal(100) - self.percent) / Decimal(100)
        return self.inner.total(lines).scale(factor)


@dataclass(frozen=True, slots=True)
class FixedDiscount:
    """Фіксована знижка; сума не може стати від'ємною."""

    inner: PriceCalculator
    amount: Money

    def total(self, lines: Sequence[OrderLine]) -> Money:
        base = self.inner.total(lines)
        if base.currency != self.amount.currency:
            raise ValueError(
                f"знижка у валюті {self.amount.currency.value}, "
                f"а кошик — {base.currency.value}"
            )
        remaining = base.amount - self.amount.amount
        return Money(max(Decimal("0.00"), remaining), base.currency)


@dataclass(frozen=True, slots=True)
class Vat:
    """Податок на додану вартість поверх попереднього калькулятора."""

    inner: PriceCalculator
    rate: Decimal

    def __post_init__(self) -> None:
        if not self.rate.is_finite() or not (Decimal(0) <= self.rate <= Decimal(1)):
            raise ValueError("ставка ПДВ має бути від 0 до 1")

    def total(self, lines: Sequence[OrderLine]) -> Money:
        return self.inner.total(lines).scale(Decimal(1) + self.rate)
