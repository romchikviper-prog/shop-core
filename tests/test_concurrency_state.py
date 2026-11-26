"""Тести спільного стану складу: гонка та два виправлення."""

from __future__ import annotations

import threading
from uuid import UUID

import pytest

from shop.concurrency.model import HOT_SKU, OrderEvent, initial_stock
from shop.concurrency.state import (
    LockedStockStore,
    OwnerStockStore,
    StockStore,
    UnsafeStockStore,
)
from shop.domain.ids import OrderId

THREADS = 8
PER_THREAD = 200


def _event(order_number: int) -> OrderEvent:
    return OrderEvent(
        seq=order_number,
        order_id=OrderId(UUID(int=order_number + 1)),
        sku=HOT_SKU,
    )


def _run_race(store: StockStore, threads: int = THREADS, per_thread: int = PER_THREAD) -> None:
    barrier = threading.Barrier(threads)

    def worker(worker_index: int) -> None:
        barrier.wait()
        for index in range(per_thread):
            store.reserve(_event(worker_index * per_thread + index))

    workers = [
        threading.Thread(target=worker, args=(worker_index,))
        for worker_index in range(threads)
    ]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()


def test_unsafe_store_oversells() -> None:
    store = UnsafeStockStore(initial_stock())
    _run_race(store)

    accepted = store.accepted_order_ids()
    rejected = store.rejected_order_ids()
    assert len(accepted) > 100
    assert len(accepted) + len(rejected) == THREADS * PER_THREAD
    assert store.available(HOT_SKU) == 0


@pytest.mark.xfail(strict=True, reason="Unsafe навмисно втрачає оновлення")
def test_unsafe_store_should_not_oversell() -> None:
    store = UnsafeStockStore(initial_stock())
    _run_race(store)

    # Ця вимога для небезпечної версії не виконується: тест падає щоразу.
    assert len(store.accepted_order_ids()) == 100


def test_locked_store_keeps_invariant() -> None:
    store = LockedStockStore(initial_stock())
    _run_race(store)

    accepted = store.accepted_order_ids()
    rejected = store.rejected_order_ids()
    assert len(accepted) == 100
    assert len(rejected) == THREADS * PER_THREAD - 100
    assert store.available(HOT_SKU) == 0
    assert store.reserved(HOT_SKU) == 100
    assert len(set(accepted)) == len(accepted)


def test_owner_store_keeps_invariant() -> None:
    with OwnerStockStore(initial_stock()) as store:
        _run_race(store)

        accepted = store.accepted_order_ids()
        rejected = store.rejected_order_ids()
        assert len(accepted) == 100
        assert len(rejected) == THREADS * PER_THREAD - 100
        assert store.available(HOT_SKU) == 0
        assert store.reserved(HOT_SKU) == 100
        assert len(set(accepted)) == len(accepted)


def test_store_reserves_order_at_most_once() -> None:
    store = LockedStockStore(initial_stock())
    event = _event(0)

    assert store.reserve(event) is True
    assert store.reserve(event) is True

    assert len(store.accepted_order_ids()) == 1
    assert store.rejected_order_ids() == ()
    assert store.available(HOT_SKU) == 99
    assert store.reserved(HOT_SKU) == 1


def test_rejected_order_is_not_counted_twice() -> None:
    store = LockedStockStore({HOT_SKU: 0})
    event = _event(0)

    assert store.reserve(event) is False
    assert store.reserve(event) is False

    assert store.accepted_order_ids() == ()
    assert len(store.rejected_order_ids()) == 1
    assert store.available(HOT_SKU) == 0
