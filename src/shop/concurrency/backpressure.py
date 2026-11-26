"""Зворотний тиск: необмежена черга проти двох політик переповнення.

Сплеск моделюється швидким виробником і повільним споживачем. Для Python
замість Java Flow порівнюються три поведінки: без межі, блокування
виробника та відкидання нових подій із лічильником відкинутих.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .model import OrderEvent


class OverflowPolicy(Enum):
    """Політика поведінки при повній черзі."""

    UNBOUNDED = "unbounded"
    BLOCK = "block"
    DROP = "drop"


@dataclass(frozen=True, slots=True)
class BackpressureResult:
    """Виміри одного прогону політики."""

    policy: OverflowPolicy
    produced: int
    accepted: int
    dropped: int
    max_depth: int
    p50_ms: float
    p99_ms: float
    producer_s: float
    consumer_s: float


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def run_policy(
    events: Sequence[OrderEvent],
    policy: OverflowPolicy,
    *,
    capacity: int = 100,
    consumer_delay_s: float = 0.0005,
) -> BackpressureResult:
    """Запускає виробника й повільного споживача з вибраній політиці."""

    if capacity < 1:
        raise ValueError("capacity має бути додатною")
    if consumer_delay_s < 0:
        raise ValueError("consumer_delay_s не може бути від'ємним")

    if policy is OverflowPolicy.UNBOUNDED:
        work_queue: queue.Queue[tuple[OrderEvent, float] | None] = queue.Queue()
    else:
        work_queue = queue.Queue(maxsize=capacity)

    accepted = 0
    dropped = 0
    max_depth = 0
    latencies_ms: list[float] = []
    consumer_started = threading.Event()

    def consumer() -> None:
        consumer_started.set()
        while True:
            item = work_queue.get()
            if item is None:
                return
            _event, enqueued_at = item
            if consumer_delay_s > 0:
                time.sleep(consumer_delay_s)
            latencies_ms.append((time.perf_counter() - enqueued_at) * 1_000.0)

    consumer_thread = threading.Thread(target=consumer, name="slow-consumer")
    consumer_thread.start()
    consumer_started.wait()

    producer_started = time.perf_counter()
    for event in events:
        item = (event, time.perf_counter())
        if policy is OverflowPolicy.DROP:
            try:
                work_queue.put_nowait(item)
                accepted += 1
            except queue.Full:
                dropped += 1
        else:
            work_queue.put(item)
            accepted += 1
        max_depth = max(max_depth, work_queue.qsize())

    work_queue.put(None)
    producer_s = time.perf_counter() - producer_started
    consumer_thread.join()
    consumer_s = time.perf_counter() - producer_started

    return BackpressureResult(
        policy=policy,
        produced=len(events),
        accepted=accepted,
        dropped=dropped,
        max_depth=max_depth,
        p50_ms=_percentile(latencies_ms, 0.50),
        p99_ms=_percentile(latencies_ms, 0.99),
        producer_s=producer_s,
        consumer_s=consumer_s,
    )
