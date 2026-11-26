"""Тести патернів стійкості без реальних очікувань."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from shop.distributed.resilience import (
    Bulkhead,
    BulkheadFullError,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    DependencyUnavailableError,
    PaymentRequest,
    ProtectedPaymentClient,
    RetryConfig,
    RetryPolicy,
)
from tests.lr3_helpers import (
    FakeClock,
    FixedJitter,
    RecordingSleeper,
    ScriptedTransport,
    success_response,
)


def make_request(key: str = "order:11111111-1111-1111-1111-111111111111:payment") -> PaymentRequest:
    return PaymentRequest(
        order_id="11111111-1111-1111-1111-111111111111",
        amount=Decimal("19999.00"),
        currency="UAH",
        idempotency_key=key,
    )


def make_client(
    transport: ScriptedTransport,
    *,
    attempts: int = 4,
    threshold: int = 10,
) -> tuple[ProtectedPaymentClient, RecordingSleeper, FakeClock]:
    clock = FakeClock()
    sleeper = RecordingSleeper()
    client = ProtectedPaymentClient(
        transport=transport,
        timeout=0.25,
        retry=RetryPolicy(
            RetryConfig(
                max_attempts=attempts,
                base_delay=0.1,
                max_delay=0.8,
                jitter_fraction=0.5,
            )
        ),
        breaker=CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=threshold, open_seconds=2.0),
            clock,
        ),
        bulkhead=Bulkhead(4),
        sleeper=sleeper,
        jitter=FixedJitter(0.0),
        clock=clock,
    )
    return client, sleeper, clock


def test_circuit_breaker_state_transitions() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        CircuitBreakerConfig(failure_threshold=3, open_seconds=2.0),
        clock,
    )
    assert breaker.state_name == "closed"

    breaker.before_call()
    breaker.on_failure()
    breaker.before_call()
    breaker.on_failure()
    breaker.before_call()
    breaker.on_failure()
    assert breaker.state_name == "open"
    with pytest.raises(CircuitOpenError):
        breaker.before_call()

    clock.advance(2.1)
    breaker.before_call()
    assert breaker.state_name == "half_open"
    breaker.on_success()
    assert breaker.state_name == "closed"

    breaker.on_failure()
    breaker.on_failure()
    breaker.on_failure()
    assert breaker.state_name == "open"
    clock.advance(2.1)
    breaker.before_call()
    assert breaker.state_name == "half_open"
    breaker.on_failure()
    assert breaker.state_name == "open"

    states = [(item.from_state, item.to_state) for item in breaker.transitions()]
    assert ("closed", "open") in states
    assert ("open", "half_open") in states
    assert ("half_open", "closed") in states
    assert ("half_open", "open") in states


def test_retry_backoff_and_attempts() -> None:
    transport = ScriptedTransport(
        [
            DependencyUnavailableError("503"),
            DependencyUnavailableError("503"),
            DependencyUnavailableError("503"),
            None,
        ]
    )
    client, sleeper, _clock = make_client(transport, attempts=4, threshold=10)
    result = client.capture(make_request())

    assert result.ok
    assert result.attempts == 4
    assert transport.calls == 4
    assert sleeper.delays == pytest.approx([0.05, 0.1, 0.2])


def test_retry_exhaustion_returns_degraded() -> None:
    transport = ScriptedTransport(
        [
            DependencyUnavailableError("503"),
            DependencyUnavailableError("503"),
            DependencyUnavailableError("503"),
            DependencyUnavailableError("503"),
        ]
    )
    client, sleeper, _clock = make_client(transport, attempts=4, threshold=10)
    result = client.capture(make_request())

    assert not result.ok
    assert result.degraded
    assert result.error_code == "dependency_unavailable"
    assert result.attempts == 4
    assert len(sleeper.delays) == 3


def test_protected_client_opens_breaker_and_stops_retries() -> None:
    transport = ScriptedTransport(
        [
            DependencyUnavailableError("503"),
            DependencyUnavailableError("503"),
            DependencyUnavailableError("503"),
            DependencyUnavailableError("503"),
        ]
    )
    client, _sleeper, _clock = make_client(transport, attempts=4, threshold=2)
    result = client.capture(make_request())

    assert not result.ok
    assert result.error_code == "circuit_open"
    assert result.attempts == 2
    assert transport.calls == 2
    stats = client.stats()
    assert stats.breaker_state == "open"
    assert stats.circuit_rejections == 1
    assert any(
        item.from_state == "closed" and item.to_state == "open"
        for item in stats.transitions
    )


def test_timeout_guard_marks_call_as_timed_out() -> None:
    clock = FakeClock()
    sleeper = RecordingSleeper()

    class SlowTransport:
        def send(self, request, timeout):
            clock.advance(0.5)
            return success_response(request)

    client = ProtectedPaymentClient(
        transport=SlowTransport(),
        timeout=0.25,
        retry=RetryPolicy(RetryConfig(max_attempts=1, base_delay=0.1)),
        breaker=CircuitBreaker(CircuitBreakerConfig(failure_threshold=3), clock),
        bulkhead=Bulkhead(2),
        sleeper=sleeper,
        jitter=FixedJitter(0.0),
        clock=clock,
    )
    result = client.capture(make_request())

    assert not result.ok
    assert result.error_code == "timeout"
    assert result.attempts == 1
    assert client.stats().breaker_state == "closed"


def test_idempotency_key_is_required_for_payment_request() -> None:
    with pytest.raises(ValueError):
        make_request("")
    with pytest.raises(ValueError):
        make_request("   ")


def test_bulkhead_limits_concurrent_calls() -> None:
    bulkhead = Bulkhead(1)
    entered = threading.Event()
    release = threading.Event()

    def hold_slot() -> None:
        with bulkhead.slot():
            entered.set()
            release.wait(timeout=2.0)

    thread = threading.Thread(target=hold_slot)
    thread.start()
    assert entered.wait(timeout=1.0)
    try:
        with pytest.raises(BulkheadFullError):
            with bulkhead.slot():
                pass
    finally:
        release.set()
        thread.join(timeout=2.0)

    assert not thread.is_alive()
