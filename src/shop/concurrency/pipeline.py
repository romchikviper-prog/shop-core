"""Конвеєри «виробник — обмежена черга — обробники».

Послідовний обробник дає базову лінію. Платформні потоки й asyncio-задачі
порівнюються на CPU- та IO-подібному навантаженні. Для CPU у Python
додатково вимірюється пул процесів, бо GIL не дає потокам паралелити
обчислення.
"""

from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import queue
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial

from .model import OrderEvent
from .state import StockStore

CPU_ROUNDS = 1000
IO_DELAY_S = 0.005


def cpu_work(event: OrderEvent, rounds: int = CPU_ROUNDS) -> str:
    """Детерміноване CPU-навантаження: кілька раундів SHA-256."""

    if rounds < 0:
        raise ValueError("rounds не може бути від'ємним")
    digest = hashlib.sha256(
        f"{event.seq}:{event.sku.value}:{event.quantity}".encode("utf-8")
    ).digest()
    for _ in range(rounds):
        digest = hashlib.sha256(digest).digest()
    return digest.hex()


def io_work(delay_s: float = IO_DELAY_S) -> None:
    """Імітація блокуючого зовнішнього виклику."""

    if delay_s < 0:
        raise ValueError("delay_s не може бути від'ємним")
    time.sleep(delay_s)


async def async_io_work(delay_s: float = IO_DELAY_S) -> None:
    """Асинхронна імітація того самого IO-очікування."""

    if delay_s < 0:
        raise ValueError("delay_s не може бути від'ємним")
    await asyncio.sleep(delay_s)


async def async_cpu_work(event: OrderEvent) -> None:
    """CPU-робота в asyncio: обчислення не віддаються іншому потоку."""

    cpu_work(event)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Результат одного прогону конвеєра."""

    events: int
    processed: int
    accepted: int
    rejected: int
    elapsed_s: float
    workers: int
    queue_capacity: int
    max_queue_depth: int

    @property
    def throughput(self) -> float:
        return self.events / self.elapsed_s if self.elapsed_s > 0 else 0.0


class _DepthTracker:
    """Максимальна спостережена глибина черги."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = 0

    def observe(self, depth: int) -> None:
        with self._lock:
            if depth > self._value:
                self._value = depth

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


def _result(
    *,
    events: int,
    processed: int,
    store: StockStore,
    elapsed_s: float,
    workers: int,
    queue_capacity: int,
    max_queue_depth: int,
) -> PipelineResult:
    accepted = len(store.accepted_order_ids())
    rejected = len(store.rejected_order_ids())
    if processed != events:
        raise AssertionError(f"оброблено {processed} з {events} подій")
    if accepted + rejected != events:
        raise AssertionError(
            f"втрачено події: прийнято {accepted}, відхилено {rejected}, усього {events}"
        )
    return PipelineResult(
        events=events,
        processed=processed,
        accepted=accepted,
        rejected=rejected,
        elapsed_s=elapsed_s,
        workers=workers,
        queue_capacity=queue_capacity,
        max_queue_depth=max_queue_depth,
    )


def run_sequential(
    events: Sequence[OrderEvent],
    store: StockStore,
    workload: Callable[[OrderEvent], None],
) -> PipelineResult:
    """Послідовний обробник: базова лінія для обох типів навантаження."""

    processed = 0
    started = time.perf_counter()
    for event in events:
        workload(event)
        store.reserve(event)
        processed += 1
    elapsed = time.perf_counter() - started
    return _result(
        events=len(events),
        processed=processed,
        store=store,
        elapsed_s=elapsed,
        workers=1,
        queue_capacity=0,
        max_queue_depth=0,
    )


def run_thread_pipeline(
    events: Sequence[OrderEvent],
    store: StockStore,
    *,
    workers: int,
    capacity: int,
    workload: Callable[[OrderEvent], None],
) -> PipelineResult:
    """Платформні потоки: виробник, обмежена черга, пул обробників."""

    if workers < 1:
        raise ValueError("workers має бути додатним")
    if capacity < 1:
        raise ValueError("capacity має бути додатною")

    work_queue: queue.Queue[OrderEvent | None] = queue.Queue(maxsize=capacity)
    processed: list[int] = []
    depth = _DepthTracker()
    started = time.perf_counter()

    def producer() -> None:
        for event in events:
            work_queue.put(event)
            depth.observe(work_queue.qsize())
        for _ in range(workers):
            work_queue.put(None)

    def consumer() -> None:
        while True:
            item = work_queue.get()
            if item is None:
                return
            workload(item)
            store.reserve(item)
            processed.append(item.seq)
            depth.observe(work_queue.qsize())

    consumers = [
        threading.Thread(target=consumer, name=f"pipeline-{index}", daemon=True)
        for index in range(workers)
    ]
    producer_thread = threading.Thread(target=producer, name="pipeline-producer")
    for thread in consumers:
        thread.start()
    producer_thread.start()
    producer_thread.join()
    for thread in consumers:
        thread.join()

    elapsed = time.perf_counter() - started
    return _result(
        events=len(events),
        processed=len(processed),
        store=store,
        elapsed_s=elapsed,
        workers=workers,
        queue_capacity=capacity,
        max_queue_depth=depth.value,
    )


async def _run_async_pipeline(
    events: Sequence[OrderEvent],
    store: StockStore,
    workers: int,
    capacity: int,
    workload: Callable[[OrderEvent], Awaitable[None]],
) -> tuple[list[int], int, float]:
    work_queue: asyncio.Queue[OrderEvent | None] = asyncio.Queue(maxsize=capacity)
    processed: list[int] = []
    max_depth = 0
    started = time.perf_counter()

    async def producer() -> None:
        nonlocal max_depth
        for event in events:
            await work_queue.put(event)
            max_depth = max(max_depth, work_queue.qsize())
        for _ in range(workers):
            await work_queue.put(None)

    async def consumer() -> None:
        nonlocal max_depth
        while True:
            item = await work_queue.get()
            if item is None:
                return
            await workload(item)
            store.reserve(item)
            processed.append(item.seq)
            max_depth = max(max_depth, work_queue.qsize())

    await asyncio.gather(producer(), *(consumer() for _ in range(workers)))
    return processed, max_depth, time.perf_counter() - started


def run_async_pipeline(
    events: Sequence[OrderEvent],
    store: StockStore,
    *,
    workers: int,
    capacity: int,
    workload: Callable[[OrderEvent], Awaitable[None]],
) -> PipelineResult:
    """Asyncio-задачі як дешевий аналог віртуальних потоків."""

    if workers < 1:
        raise ValueError("workers має бути додатним")
    if capacity < 1:
        raise ValueError("capacity має бути додатною")

    processed, max_depth, elapsed = asyncio.run(
        _run_async_pipeline(events, store, workers, capacity, workload)
    )
    return _result(
        events=len(events),
        processed=len(processed),
        store=store,
        elapsed_s=elapsed,
        workers=workers,
        queue_capacity=capacity,
        max_queue_depth=max_depth,
    )


def run_multiprocessing_cpu(
    events: Sequence[OrderEvent],
    store: StockStore,
    *,
    workers: int,
    rounds: int = CPU_ROUNDS,
) -> PipelineResult:
    """CPU-робота в пулі процесів; резервування виконує батьківський процес.

    Це дозволене завданням спрощення: процеси не ділять пам'ять, тому
    спільний стан складу залишається в одному процесі-власнику.
    """

    if workers < 1:
        raise ValueError("workers має бути додатним")
    chunksize = max(1, len(events) // (workers * 4))
    worker = partial(cpu_work, rounds=rounds)
    started = time.perf_counter()
    with multiprocessing.Pool(processes=workers) as pool:
        _digests = pool.map(worker, events, chunksize=chunksize)
    processed = 0
    for event in events:
        store.reserve(event)
        processed += 1
    elapsed = time.perf_counter() - started
    return _result(
        events=len(events),
        processed=processed,
        store=store,
        elapsed_s=elapsed,
        workers=workers,
        queue_capacity=0,
        max_queue_depth=0,
    )
