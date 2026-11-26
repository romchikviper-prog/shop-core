"""Тести конвеєрів: усі події оброблені, інваріант збережений."""

from __future__ import annotations

from collections.abc import Sequence

from shop.concurrency.model import HOT_SKU, OrderEvent, generate_events, initial_stock
from shop.concurrency.pipeline import (
    cpu_work,
    run_async_pipeline,
    run_thread_pipeline,
)
from shop.concurrency.state import LockedStockStore


def _check_pipeline(
    store: LockedStockStore,
    events: Sequence[OrderEvent],
) -> None:
    accepted = store.accepted_order_ids()
    rejected = store.rejected_order_ids()
    hot_ids = {event.order_id for event in events if event.sku == HOT_SKU}
    hot_accepted = [order_id for order_id in accepted if order_id in hot_ids]
    assert len(hot_accepted) == 100
    assert len(accepted) + len(rejected) == len(events)
    assert len(set(accepted)) == len(accepted)
    assert store.available(HOT_SKU) == 0
    assert store.reserved(HOT_SKU) == 100


def test_thread_pipeline_processes_every_event() -> None:
    events = generate_events(5_000)
    store = LockedStockStore(initial_stock())

    result = run_thread_pipeline(
        events,
        store,
        workers=8,
        capacity=100,
        workload=lambda event: cpu_work(event, rounds=2),
    )

    assert result.processed == 5_000
    assert result.accepted + result.rejected == 5_000
    assert result.max_queue_depth <= 100
    _check_pipeline(store, events)


def test_async_pipeline_processes_every_event() -> None:
    events = generate_events(5_000)
    store = LockedStockStore(initial_stock())

    async def workload(event: OrderEvent) -> None:
        cpu_work(event, rounds=2)

    result = run_async_pipeline(
        events,
        store,
        workers=16,
        capacity=100,
        workload=workload,
    )

    assert result.processed == 5_000
    assert result.accepted + result.rejected == 5_000
    assert result.max_queue_depth <= 100
    _check_pipeline(store, events)
