"""Агрегат «Залишок на складі» та запит на резервування."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .errors import DomainError, InsufficientStock, InvalidValue
from .ids import Sku
from .result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class StockRequest:
    """Потреба в товарі для одного замовлення."""

    sku: Sku
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise ValueError("кількість у запиті має бути додатною")


@dataclass(frozen=True, slots=True)
class StockItem:
    """Залишок одного артикулу: доступно та зарезервовано."""

    sku: Sku
    available: int
    reserved: int = 0

    def __post_init__(self) -> None:
        if self.available < 0:
            raise ValueError("available не може бути від'ємним")
        if self.reserved < 0:
            raise ValueError("reserved не може бути від'ємним")

    def reserve(self, quantity: int) -> Result[StockItem, DomainError]:
        """Резервує одиниці товару, не допускаючи від'ємного залишку."""
        if quantity < 1:
            return Err(InvalidValue("quantity", "кількість має бути додатною"))
        if self.available < quantity:
            return Err(InsufficientStock(self.sku, quantity, self.available))
        return Ok(
            replace(
                self,
                available=self.available - quantity,
                reserved=self.reserved + quantity,
            )
        )

    def release(self, quantity: int) -> Result[StockItem, DomainError]:
        """Повертає раніше зарезервовані одиниці в доступний залишок."""
        if quantity < 1:
            return Err(InvalidValue("quantity", "кількість має бути додатною"))
        if self.reserved < quantity:
            return Err(
                InvalidValue(
                    "quantity",
                    f"звільняється {quantity}, а зарезервовано лише {self.reserved}",
                )
            )
        return Ok(
            replace(
                self,
                available=self.available + quantity,
                reserved=self.reserved - quantity,
            )
        )
