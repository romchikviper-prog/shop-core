"""Порти (межі) між ядром домену та зовнішньою інфраструктурою.

Ядро залежить лише від цих інтерфейсів. Репозиторій, склад і платіжний
шлюз підставляються в конструктор сервісу, тому в тестах їх замінюють
на пам'ять або на контролери, не змінюючи домен.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeAlias, TypeVar

from shop.domain.errors import DomainError
from shop.domain.ids import OrderId
from shop.domain.inventory import StockRequest
from shop.domain.money import Money
from shop.domain.order import Order
from shop.domain.payment import PaymentReceipt
from shop.domain.result import Result

TA = TypeVar("TA")
TId = TypeVar("TId", contravariant=True)


@dataclass(frozen=True, slots=True)
class PaymentRequest:
    """Запит на списання коштів через платіжний шлюз."""

    order_id: OrderId
    amount: Money
    token: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise ValueError("token не може бути порожнім")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key не може бути порожнім")


class Clock(Protocol):
    """Джерело поточного часу; у тестах підставляється фіксований годинник."""

    def now(self) -> datetime: ...


class InventoryPort(Protocol):
    """Резервування та звільнення залишків на складі."""

    def reserve(
        self, order_id: OrderId, requests: Sequence[StockRequest]
    ) -> Result[None, DomainError]: ...

    def release(self, order_id: OrderId) -> None: ...


class PaymentGateway(Protocol):
    """Зовнішній платіжний шлюз."""

    def charge(self, request: PaymentRequest) -> Result[PaymentReceipt, DomainError]: ...


class Repository(Protocol[TA, TId]):
    """Узагальнений порт збереження агрегатів."""

    def get(self, aggregate_id: TId) -> TA | None: ...

    def save(self, aggregate: TA) -> None: ...


OrderRepository: TypeAlias = Repository[Order, OrderId]
