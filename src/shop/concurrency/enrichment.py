"""Асинхронне збагачення замовлення трьома зовнішніми викликами.

Платіжний шлюз (≈80 мс), резервування на складі (≈30 мс) і доставка
(≈40 мс, іноді відмовляє) запускаються паралельно. Кожен виклик має
власний тайм-аут і резервне значення; результат позначається partial,
якщо хоч один виклик не повернув справжнє значення.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol

from shop.domain.ids import OrderId

from .model import OrderEvent

PAYMENT_FALLBACK = "payment:fallback"
WAREHOUSE_FALLBACK = "warehouse:fallback"
DELIVERY_FALLBACK = "delivery:fallback"


class ServiceError(Exception):
    """Зовнішній сервіс відмовив або недоступний."""


class AsyncService(Protocol):
    """Мінімальний контракт зовнішнього сервісу."""

    async def call(self, event: OrderEvent) -> str: ...


@dataclass(frozen=True, slots=True)
class ServiceResult:
    """Значення виклику та ознака, що це резервне значення."""

    value: str
    fallback: bool


@dataclass(frozen=True, slots=True)
class EnrichedOrder:
    """Результат збагачення однієї події."""

    order_id: OrderId
    payment: ServiceResult
    warehouse: ServiceResult
    delivery: ServiceResult

    @property
    def partial(self) -> bool:
        return self.payment.fallback or self.warehouse.fallback or self.delivery.fallback


class SimulatedService:
    """Сервіс із затримкою; delay_s=0 і fail=True дають швидку відмову."""

    def __init__(self, name: str, delay_s: float, *, fail: bool = False) -> None:
        if delay_s < 0:
            raise ValueError("delay_s не може бути від'ємним")
        if not name.strip():
            raise ValueError("name не може бути порожнім")
        self.name = name
        self.delay_s = delay_s
        self.fail = fail

    async def call(self, event: OrderEvent) -> str:
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        if self.fail:
            raise ServiceError(f"{self.name} недоступний")
        return f"{self.name}:{event.order_id}"


class ManualService:
    """Керований сервіс для тестів: відповідь звільняє сам тест."""

    def __init__(self, value: str, *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.started = asyncio.Event()
        self._release = asyncio.Event()

    async def call(self, event: OrderEvent) -> str:
        del event
        self.started.set()
        await self._release.wait()
        if self.fail:
            raise ServiceError(f"{self.value} недоступний")
        return self.value

    def release(self) -> None:
        self._release.set()


async def _guarded(
    call: Awaitable[str],
    fallback: str,
    timeout_s: float,
) -> ServiceResult:
    """Чекає на coroutine не довше timeout_s, інакше повертає fallback."""

    try:
        value = await asyncio.wait_for(call, timeout=timeout_s)
    except (TimeoutError, ServiceError):
        return ServiceResult(value=fallback, fallback=True)
    return ServiceResult(value=value, fallback=False)


async def enrich(
    event: OrderEvent,
    payment: AsyncService,
    warehouse: AsyncService,
    delivery: AsyncService,
    *,
    timeout_s: float = 0.1,
) -> EnrichedOrder:
    """Паралельний варіант: три виклики стартують одночасно."""

    if timeout_s <= 0:
        raise ValueError("timeout_s має бути додатним")

    payment_task = asyncio.create_task(
        _guarded(payment.call(event), PAYMENT_FALLBACK, timeout_s)
    )
    warehouse_task = asyncio.create_task(
        _guarded(warehouse.call(event), WAREHOUSE_FALLBACK, timeout_s)
    )
    delivery_task = asyncio.create_task(
        _guarded(delivery.call(event), DELIVERY_FALLBACK, timeout_s)
    )
    payment_result, warehouse_result, delivery_result = await asyncio.gather(
        payment_task,
        warehouse_task,
        delivery_task,
    )
    return EnrichedOrder(
        order_id=event.order_id,
        payment=payment_result,
        warehouse=warehouse_result,
        delivery=delivery_result,
    )


async def enrich_sequential(
    event: OrderEvent,
    payment: AsyncService,
    warehouse: AsyncService,
    delivery: AsyncService,
    *,
    timeout_s: float = 0.1,
) -> EnrichedOrder:
    """Послідовний варіант для порівняння затримки."""

    if timeout_s <= 0:
        raise ValueError("timeout_s має бути додатним")

    payment_result = await _guarded(
        payment.call(event), PAYMENT_FALLBACK, timeout_s
    )
    warehouse_result = await _guarded(
        warehouse.call(event), WAREHOUSE_FALLBACK, timeout_s
    )
    delivery_result = await _guarded(
        delivery.call(event), DELIVERY_FALLBACK, timeout_s
    )
    return EnrichedOrder(
        order_id=event.order_id,
        payment=payment_result,
        warehouse=warehouse_result,
        delivery=delivery_result,
    )
