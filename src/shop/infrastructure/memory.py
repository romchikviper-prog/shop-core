"""Адаптери в пам'яті для тестів і демонстраційного запуску."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from shop.domain.errors import DomainError, InsufficientStock, InvalidValue
from shop.domain.ids import OrderId, Sku
from shop.domain.inventory import StockItem, StockRequest
from shop.domain.order import Order
from shop.domain.result import Err, Ok, Result


class InMemoryOrderRepository:
    """Репозиторій замовлень у пам'яті."""

    def __init__(self) -> None:
        self._orders: dict[OrderId, Order] = {}

    def get(self, aggregate_id: OrderId) -> Order | None:
        return self._orders.get(aggregate_id)

    def save(self, aggregate: Order) -> None:
        self._orders[aggregate.order_id] = aggregate

    def all(self) -> tuple[Order, ...]:
        """Усі збережені замовлення (для демонстрації)."""
        return tuple(self._orders.values())


class InMemoryInventory:
    """Склад у пам'яті з ідемпотентним резервуванням за номером замовлення."""

    def __init__(self, stock: Mapping[Sku, int]) -> None:
        self._stock: dict[Sku, StockItem] = {
            sku: StockItem(sku=sku, available=quantity) for sku, quantity in stock.items()
        }
        self._reservations: dict[OrderId, tuple[StockRequest, ...]] = {}

    def reserve(
        self, order_id: OrderId, requests: Sequence[StockRequest]
    ) -> Result[None, DomainError]:
        requested = tuple(requests)
        existing = self._reservations.get(order_id)
        if existing is not None:
            if existing == requested:
                return Ok(None)
            return Err(
                InvalidValue(
                    "reservation",
                    f"замовлення {order_id} вже має інший набір резервувань",
                )
            )

        for request in requested:
            item = self._stock.get(request.sku)
            available = item.available if item is not None else 0
            if available < request.quantity:
                return Err(
                    InsufficientStock(request.sku, request.quantity, available)
                )

        for request in requested:
            item = self._stock[request.sku]
            match item.reserve(request.quantity):
                case Ok(updated):
                    self._stock[request.sku] = updated
                case Err(error):
                    raise AssertionError(f"резервування після перевірки не вдалося: {error}")

        self._reservations[order_id] = requested
        return Ok(None)

    def release(self, order_id: OrderId) -> None:
        requested = self._reservations.pop(order_id, None)
        if requested is None:
            return
        for request in requested:
            item = self._stock[request.sku]
            match item.release(request.quantity):
                case Ok(updated):
                    self._stock[request.sku] = updated
                case Err(error):
                    raise AssertionError(f"звільнення резервування не вдалося: {error}")

    def stock_item(self, sku: Sku) -> StockItem | None:
        """Поточний залишок артикулу."""
        return self._stock.get(sku)

    def available(self, sku: Sku) -> int:
        """Скільки одиниць доступно до продажу."""
        item = self._stock.get(sku)
        return item.available if item is not None else 0
