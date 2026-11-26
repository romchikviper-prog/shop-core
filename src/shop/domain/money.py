"""Гроші як незмінний Value Object: сума й валюта разом.

Інваріанти: сума скінченна, невід'ємна й нормалізована до двох знаків.
Операції з різними валютами заборонені на рівні винятку (порушення контракту),
а розбір зовнішнього рядка повертає Result, бо це очікувана помилка.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum

from .errors import DomainError, InvalidValue
from .result import Err, Ok, Result

_CENTS = Decimal("0.01")


class Currency(Enum):
    """Валюти, з якими працює магазин."""

    UAH = "UAH"
    USD = "USD"
    EUR = "EUR"


@dataclass(frozen=True, slots=True)
class Money:
    """Сума грошей у конкретній валюті."""

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("amount має бути Decimal")
        if not self.amount.is_finite():
            raise ValueError("amount має бути скінченним числом")
        if self.amount < 0:
            raise ValueError("amount не може бути від'ємним")
        object.__setattr__(
            self,
            "amount",
            self.amount.quantize(_CENTS, rounding=ROUND_HALF_UP),
        )

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        """Нульова сума в заданій валюті."""
        return cls(Decimal("0.00"), currency)

    def plus(self, other: Money) -> Money:
        """Додає суму тієї самої валюти."""
        if self.currency != other.currency:
            raise ValueError(
                f"не можна додати {other.currency.value} до {self.currency.value}"
            )
        return Money(self.amount + other.amount, self.currency)

    def scale(self, factor: Decimal) -> Money:
        """Множить суму на невід'ємний коефіцієнт (знижка, податок)."""
        if not factor.is_finite() or factor < 0:
            raise ValueError("коефіцієнт має бути скінченним і невід'ємним")
        return Money(self.amount * factor, self.currency)

    def times(self, quantity: int) -> Money:
        """Вартість кількох одиниць товару."""
        if quantity < 1:
            raise ValueError("кількість має бути додатною")
        return Money(self.amount * quantity, self.currency)

    def is_zero(self) -> bool:
        """Чи дорівнює сума нулю."""
        return self.amount == 0

    def __str__(self) -> str:
        return f"{self.amount:.2f} {self.currency.value}"

    @classmethod
    def parse(cls, raw: str, currency: Currency) -> Result[Money, DomainError]:
        """Розбирає суму із зовнішнього рядка, не кидаючи винятків."""
        text = raw.strip().replace(",", ".")
        if not text:
            return Err(InvalidValue("amount", "порожнє значення"))
        try:
            amount = Decimal(text)
        except InvalidOperation:
            return Err(InvalidValue("amount", f"некоректне число: {raw!r}"))
        if not amount.is_finite():
            return Err(InvalidValue("amount", "число має бути скінченним"))
        if amount < 0:
            return Err(InvalidValue("amount", "сума не може бути від'ємною"))
        if amount != amount.quantize(_CENTS):
            return Err(InvalidValue("amount", "не більше двох знаків після коми"))
        return Ok(cls(amount, currency))
