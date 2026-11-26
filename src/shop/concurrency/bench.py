"""Вимірювання для звіту ЛР2.

Запуск:
    $env:PYTHONPATH="src"; py -3 -m shop.concurrency.bench

Скрипт друкує таблиці, з яких числа переносяться в README. Для IO-базової
лінії використовується менше подій, бо кожна подія блокує 5 мс.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import platform
import statistics
import threading
import time
from collections.abc import Callable, Coroutine, Sequence
from typing import Any
from uuid import UUID

from shop.domain.ids import OrderId

from .backpressure import OverflowPolicy, run_policy
from .enrichment import SimulatedService, enrich, enrich_sequential
from .model import HOT_SKU, OrderEvent, generate_events, initial_stock
from .pipeline import (
    CPU_ROUNDS,
    IO_DELAY_S,
    PipelineResult,
    async_cpu_work,
    async_io_work,
    cpu_work,
    io_work,
    run_async_pipeline,
    run_multiprocessing_cpu,
    run_sequential,
    run_thread_pipeline,
)
from .state import (
    LockedStockStore,
    OwnerStockStore,
    StockStore,
    UnsafeStockStore,
)

RACE_THREADS = 8
RACE_PER_THREAD = 5_000


def _cpu_workload(event: OrderEvent) -> None:
    cpu_work(event)


def _median(values: Sequence[float]) -> float:
    return statistics.median(values)


def _p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * 0.95))
    return ordered[index]


def _range(values: Sequence[float]) -> str:
    return f"{min(values):.3f}–{max(values):.3f}"


def _print_pipeline_table(
    title: str,
    rows: Sequence[tuple[str, Callable[[], PipelineResult], int]],
) -> None:
    print(f"\n{title}")
    print(
        f"{'Конфігурація':<34} {'Подій/с':>10} {'мін–макс':>18} "
        f"{'Прийнято':>9} {'Відхилено':>10} {'Черга':>7}"
    )
    for label, factory, repeats in rows:
        results = [factory() for _ in range(repeats)]
        throughputs = [result.throughput for result in results]
        first = results[0]
        print(
            f"{label:<34} {_median(throughputs):>10.0f} "
            f"{_range(throughputs):>18} {first.accepted:>9} "
            f"{first.rejected:>10} {first.max_queue_depth:>7}"
        )


def _race_once(store: StockStore, threads: int, per_thread: int) -> tuple[int, int, float]:
    barrier = threading.Barrier(threads)

    def worker(worker_index: int) -> None:
        barrier.wait()
        for index in range(per_thread):
            number = worker_index * per_thread + index
            store.reserve(
                OrderEvent(
                    seq=number,
                    order_id=OrderId(UUID(int=number + 1)),
                    sku=HOT_SKU,
                )
            )

    workers = [
        threading.Thread(target=worker, args=(worker_index,))
        for worker_index in range(threads)
    ]
    started = time.perf_counter()
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    elapsed = time.perf_counter() - started
    return len(store.accepted_order_ids()), len(store.rejected_order_ids()), elapsed


def _print_race_table() -> None:
    print("\nКрок 2. Гонка даних: 8 потоків × 5 000 спроб, гарячий товар = 100 одиниць")

    def unsafe_factory() -> tuple[int, int, float]:
        return _race_once(UnsafeStockStore(initial_stock()), RACE_THREADS, RACE_PER_THREAD)

    def locked_factory() -> tuple[int, int, float]:
        return _race_once(LockedStockStore(initial_stock()), RACE_THREADS, RACE_PER_THREAD)

    def owner_factory() -> tuple[int, int, float]:
        with OwnerStockStore(initial_stock()) as store:
            return _race_once(store, RACE_THREADS, RACE_PER_THREAD)

    print(
        f"{'Реалізація':<22} {'Медіана, мс':>12} {'Прийнято (медіана)':>20} "
        f"{'Перевищень 100':>16}"
    )
    for label, factory in (
        ("Unsafe", unsafe_factory),
        ("Locked", locked_factory),
        ("Owner", owner_factory),
    ):
        samples = [factory() for _ in range(10)]
        times = [sample[2] * 1_000.0 for sample in samples]
        accepted = [sample[0] for sample in samples]
        oversell = sum(1 for value in accepted if value > 100)
        print(
            f"{label:<22} {_median(times):>12.1f} {_median(accepted):>20.1f} "
            f"{oversell:>13}/10"
        )


def _services(scenario: str) -> tuple[SimulatedService, SimulatedService, SimulatedService]:
    if scenario == "normal":
        return (
            SimulatedService("payment", 0.080),
            SimulatedService("warehouse", 0.030),
            SimulatedService("delivery", 0.040),
        )
    if scenario == "slow":
        return (
            SimulatedService("payment", 0.080),
            SimulatedService("warehouse", 0.030),
            SimulatedService("delivery", 0.400),
        )
    if scenario == "fail":
        return (
            SimulatedService("payment", 0.080),
            SimulatedService("warehouse", 0.030),
            SimulatedService("delivery", 0.040, fail=True),
        )
    raise ValueError(f"невідомий сценарій: {scenario}")


def _time_async(
    factory: Callable[[], Coroutine[Any, Any, None]],
    repeats: int,
) -> list[float]:
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        asyncio.run(factory())
        samples.append((time.perf_counter() - started) * 1_000.0)
    return samples


def _print_enrichment_table() -> None:
    print("\nКрок 4. Асинхронне збагачення: три виклики, тайм-аут 100 мс")
    print(
        f"{'Сценарій':<26} {'Послідовно, мс':>16} {'Паралельно, мс':>16} "
        f"{'p95 парал., мс':>15} {'partial':>9}"
    )
    event = generate_events(1)[0]
    for scenario, repeats in (("normal", 20), ("slow", 10), ("fail", 10)):
        async def sequential_factory(scenario_name: str = scenario) -> None:
            payment, warehouse, delivery = _services(scenario_name)
            await enrich_sequential(
                event, payment, warehouse, delivery, timeout_s=0.1
            )

        async def parallel_factory(scenario_name: str = scenario) -> None:
            payment, warehouse, delivery = _services(scenario_name)
            await enrich(event, payment, warehouse, delivery, timeout_s=0.1)

        sequential = _time_async(sequential_factory, repeats)
        parallel = _time_async(parallel_factory, repeats)
        print(
            f"{scenario:<26} {_median(sequential):>16.1f} "
            f"{_median(parallel):>16.1f} {_p95(parallel):>15.1f} "
            f"{'так' if scenario != 'normal' else 'ні':>9}"
        )


def _print_backpressure_table() -> None:
    events = generate_events(5_000)
    print("\nКрок 5. Зворотний тиск: 5 000 подій, споживач 0,5 мс на подію")
    print(
        f"{'Політика':<24} {'Прийнято':>9} {'Відкинуто':>10} "
        f"{'Макс. глибина':>14} {'p50, мс':>9} {'p99, мс':>9} "
        f"{'Виробник, с':>12}"
    )
    for policy in (
        OverflowPolicy.UNBOUNDED,
        OverflowPolicy.BLOCK,
        OverflowPolicy.DROP,
    ):
        result = run_policy(events, policy, capacity=100, consumer_delay_s=0.0005)
        print(
            f"{policy.value:<24} {result.accepted:>9} {result.dropped:>10} "
            f"{result.max_depth:>14} {result.p50_ms:>9.1f} "
            f"{result.p99_ms:>9.1f} {result.producer_s:>12.2f}"
        )


def main() -> int:
    cpu_workers = os.cpu_count() or 1
    print("Стенд ЛР2")
    print(f"Python: {platform.python_version()}")
    print(f"ОС: {platform.platform()}")
    print(f"Логічних CPU: {cpu_workers}")
    print(f"CPU-раунд: {CPU_ROUNDS} SHA-256; IO-затримка: {IO_DELAY_S * 1000:.0f} мс")

    cpu_events = generate_events(20_000)
    io_events = generate_events(2_000)
    sale_events = generate_events(5_000)

    def baseline_cpu() -> PipelineResult:
        return run_sequential(cpu_events, LockedStockStore(initial_stock()), _cpu_workload)

    def baseline_io() -> PipelineResult:
        return run_sequential(io_events, LockedStockStore(initial_stock()), lambda _event: io_work(IO_DELAY_S))

    _print_pipeline_table(
        "Крок 1. Базова лінія (послідовно)",
        (
            ("CPU, 20 000 подій", baseline_cpu, 2),
            ("IO 5 мс, 2 000 подій", baseline_io, 1),
        ),
    )

    _print_race_table()

    _print_pipeline_table(
        "Крок 3. Конвеєр, CPU-подібне навантаження (5 000 подій, 12 логічних CPU)",
        (
            (
                "послідовно",
                lambda: run_sequential(sale_events, LockedStockStore(initial_stock()), _cpu_workload),
                3,
            ),
            (
                "платформні потоки ×12",
                lambda: run_thread_pipeline(
                    sale_events,
                    LockedStockStore(initial_stock()),
                    workers=cpu_workers,
                    capacity=100,
                    workload=_cpu_workload,
                ),
                3,
            ),
            (
                "asyncio-задачі ×12",
                lambda: run_async_pipeline(
                    sale_events,
                    LockedStockStore(initial_stock()),
                    workers=cpu_workers,
                    capacity=100,
                    workload=async_cpu_work,
                ),
                3,
            ),
            (
                "процеси ×12",
                lambda: run_multiprocessing_cpu(
                    sale_events,
                    LockedStockStore(initial_stock()),
                    workers=cpu_workers,
                ),
                3,
            ),
        ),
    )

    _print_pipeline_table(
        "Крок 3. Конвеєр, IO-подібне навантаження (5 000 подій, 5 мс на подію)",
        (
            (
                "послідовно",
                lambda: run_sequential(
                    sale_events,
                    LockedStockStore(initial_stock()),
                    lambda _event: io_work(IO_DELAY_S),
                ),
                1,
            ),
            (
                "платформні потоки ×64",
                lambda: run_thread_pipeline(
                    sale_events,
                    LockedStockStore(initial_stock()),
                    workers=64,
                    capacity=100,
                    workload=lambda _event: io_work(IO_DELAY_S),
                ),
                3,
            ),
            (
                "asyncio-задачі ×2000",
                lambda: run_async_pipeline(
                    sale_events,
                    LockedStockStore(initial_stock()),
                    workers=2_000,
                    capacity=100,
                    workload=lambda _event: async_io_work(IO_DELAY_S),
                ),
                3,
            ),
        ),
    )

    _print_enrichment_table()
    _print_backpressure_table()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
