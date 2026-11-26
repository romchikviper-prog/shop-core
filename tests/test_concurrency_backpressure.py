"""Тести зворотного тиску: межа черги й політика переповнення."""

from __future__ import annotations

from shop.concurrency.backpressure import OverflowPolicy, run_policy
from shop.concurrency.model import generate_events


def test_blocking_queue_accepts_all() -> None:
    events = generate_events(100)
    result = run_policy(
        events,
        OverflowPolicy.BLOCK,
        capacity=10,
        consumer_delay_s=0.0,
    )

    assert result.accepted == 100
    assert result.dropped == 0
    assert result.max_depth <= 10
    assert result.accepted + result.dropped == result.produced


def test_drop_policy_counts_dropped_and_bounds_queue() -> None:
    events = generate_events(300)
    result = run_policy(
        events,
        OverflowPolicy.DROP,
        capacity=10,
        consumer_delay_s=0.001,
    )

    assert result.accepted + result.dropped == 300
    assert result.dropped > 0
    assert result.max_depth <= 10
    assert result.accepted >= 1


def test_unbounded_queue_grows_beyond_capacity() -> None:
    events = generate_events(300)
    result = run_policy(
        events,
        OverflowPolicy.UNBOUNDED,
        capacity=10,
        consumer_delay_s=0.001,
    )

    assert result.accepted == 300
    assert result.dropped == 0
    assert result.max_depth > 10
    assert result.accepted + result.dropped == result.produced
