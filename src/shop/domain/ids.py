"""Ідентифікатори та артикули як незмінні Value Object-и.

Конструктор захищає інваріант і падає на порушенні контракту програмою,
а parse призначений для зовнішніх даних і повертає Result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from .errors import DomainError, InvalidValue
from .result import Err, Ok, Result

_SKU_PATTERN = re.compile(r"^[A-Z]{3}-\d{4}$")


@dataclass(frozen=True, slots=True)
class Sku:
    """Артикул товару у форматі ABC-1234."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _SKU_PATTERN.fullmatch(self.value):
            raise ValueError(f"некоректний артикул: {self.value!r}")

    @classmethod
    def parse(cls, raw: str) -> Result[Sku, DomainError]:
        """Розбирає артикул із зовнішнього джерела."""
        text = raw.strip()
        if not _SKU_PATTERN.fullmatch(text):
            return Err(InvalidValue("sku", f"очікується формат ABC-1234, отримано {raw!r}"))
        return Ok(cls(text))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class OrderId:
    """Ідентифікатор замовлення."""

    value: UUID

    @classmethod
    def new(cls) -> OrderId:
        """Генерує новий ідентифікатор."""
        return cls(uuid4())

    @classmethod
    def parse(cls, raw: str) -> Result[OrderId, DomainError]:
        """Розбирає UUID замовлення із зовнішнього джерела."""
        try:
            value = UUID(raw)
        except (ValueError, AttributeError, TypeError):
            return Err(InvalidValue("order_id", f"некоректний UUID: {raw!r}"))
        return Ok(cls(value))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class CustomerId:
    """Ідентифікатор покупця."""

    value: UUID

    @classmethod
    def new(cls) -> CustomerId:
        """Генерує новий ідентифікатор покупця."""
        return cls(uuid4())

    @classmethod
    def parse(cls, raw: str) -> Result[CustomerId, DomainError]:
        """Розбирає UUID покупця із зовнішнього джерела."""
        try:
            value = UUID(raw)
        except (ValueError, AttributeError, TypeError):
            return Err(InvalidValue("customer_id", f"некоректний UUID: {raw!r}"))
        return Ok(cls(value))

    def __str__(self) -> str:
        return str(self.value)
