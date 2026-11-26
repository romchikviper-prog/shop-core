"""Тести асинхронного збагачення: тайм-аут, відмова, паралельність."""

from __future__ import annotations

import asyncio
from uuid import UUID

from shop.concurrency.enrichment import (
    DELIVERY_FALLBACK,
    ManualService,
    SimulatedService,
    enrich,
)
from shop.concurrency.model import HOT_SKU, OrderEvent
from shop.domain.ids import OrderId


def _event() -> OrderEvent:
    return OrderEvent(seq=0, order_id=OrderId(UUID(int=1)), sku=HOT_SKU)


def test_timeout_returns_fallback_and_partial() -> None:
    async def scenario() -> None:
        result = await enrich(
            _event(),
            SimulatedService("payment", 0.0),
            SimulatedService("warehouse", 0.0),
            ManualService("delivery"),
            timeout_s=0.02,
        )
        assert result.delivery.fallback is True
        assert result.delivery.value == DELIVERY_FALLBACK
        assert result.partial is True
        assert result.payment.fallback is False

    asyncio.run(scenario())


def test_failing_service_returns_fallback() -> None:
    async def scenario() -> None:
        result = await enrich(
            _event(),
            SimulatedService("payment", 0.0),
            SimulatedService("warehouse", 0.0),
            SimulatedService("delivery", 0.0, fail=True),
            timeout_s=0.1,
        )
        assert result.delivery.fallback is True
        assert result.delivery.value == DELIVERY_FALLBACK
        assert result.partial is True
        assert result.payment.fallback is False
        assert result.warehouse.fallback is False

    asyncio.run(scenario())


def test_all_calls_start_before_any_completes() -> None:
    async def scenario() -> None:
        payment = ManualService("payment")
        warehouse = ManualService("warehouse")
        delivery = ManualService("delivery")
        task = asyncio.create_task(
            enrich(
                _event(),
                payment,
                warehouse,
                delivery,
                timeout_s=1.0,
            )
        )

        await asyncio.wait_for(
            asyncio.gather(
                payment.started.wait(),
                warehouse.started.wait(),
                delivery.started.wait(),
            ),
            timeout=1.0,
        )

        payment.release()
        warehouse.release()
        delivery.release()
        result = await task
        assert result.partial is False

    asyncio.run(scenario())
