"""Патерни стійкості: Timeout, Retry, Circuit Breaker, Bulkhead.

Композиція в `ProtectedPaymentClient` така:
Bulkhead → Retry → (Circuit Breaker → Timeout → HTTP).
Тобто семафор обмежує одночасні виклики, повторювач робить паузи, а
кожна спроба проходить через аварійний вимикач і тайм-аут. Коли коло
розімкнене, повторювач одразу отримує відмову й не добиває сервіс.
"""

from __future__ import annotations

import http.client
import json
import random
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Protocol, TypeVar

from .jsonutil import (
    JsonObject,
    as_bool,
    as_object,
    as_str,
    datetime_from_json,
    field,
)

T = TypeVar("T")


class PaymentCallError(Exception):
    """Базова помилка виклику залежного сервісу."""


class RetryablePaymentError(PaymentCallError):
    """Тимчасова помилка, яку має сенс повторити."""


class TimeoutPaymentError(RetryablePaymentError):
    """Виклик перевищив дозволений час."""


class TransportPaymentError(RetryablePaymentError):
    """Мережева помилка або неочікувана відповідь."""


class DependencyUnavailableError(RetryablePaymentError):
    """Залежний сервіс відповів 5xx."""


class PaymentDeclinedError(PaymentCallError):
    """Сервіс відхилив запит; повторювати не можна."""


class IdempotencyConflictError(PaymentDeclinedError):
    """Ключ ідемпотентності вже використано з іншими даними."""


class CircuitOpenError(PaymentCallError):
    """Аварійний вимикач не пропускає виклик."""


class BulkheadFullError(PaymentCallError):
    """Вичерпано ліміт одночасних викликів."""


@dataclass(frozen=True, slots=True)
class PaymentRequest:
    """Запит до платіжного шлюзу."""

    order_id: str
    amount: Decimal
    currency: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("order_id не може бути порожнім")
        if not self.amount.is_finite() or self.amount < 0:
            raise ValueError("amount має бути скінченним і невід'ємним")
        if self.currency not in ("UAH", "USD", "EUR"):
            raise ValueError(f"невідома валюта: {self.currency!r}")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key не може бути порожнім")


@dataclass(frozen=True, slots=True)
class PaymentResponse:
    """Успішна відповідь платіжного шлюзу."""

    payment_id: str
    order_id: str
    status: str
    amount: Decimal
    currency: str
    captured_at: datetime
    idempotent_replay: bool

    def __post_init__(self) -> None:
        if not self.payment_id.strip():
            raise ValueError("payment_id не може бути порожнім")
        if self.status != "captured":
            raise ValueError(f"неочікуваний статус платежу: {self.status!r}")


@dataclass(frozen=True, slots=True)
class PaymentCallResult:
    """Результат виклику з погляду ядра."""

    ok: bool
    response: PaymentResponse | None
    error_code: str | None
    error_message: str | None
    attempts: int
    degraded: bool

    @classmethod
    def success(cls, response: PaymentResponse, attempts: int) -> PaymentCallResult:
        """Успішний платіж."""
        return cls(
            ok=True,
            response=response,
            error_code=None,
            error_message=None,
            attempts=attempts,
            degraded=False,
        )

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        attempts: int,
    ) -> PaymentCallResult:
        """Помилка; ядро відповість деградовано."""
        return cls(
            ok=False,
            response=None,
            error_code=code,
            error_message=message,
            attempts=attempts,
            degraded=True,
        )


@dataclass(frozen=True, slots=True)
class BreakerTransition:
    """Запис переходу стану аварійного вимикача."""

    from_state: str
    to_state: str
    at: float
    cause: str

    def to_json(self) -> JsonObject:
        """Серіалізує перехід."""
        return {
            "from": self.from_state,
            "to": self.to_state,
            "at": round(self.at, 6),
            "cause": self.cause,
        }


@dataclass(frozen=True, slots=True)
class ClientStats:
    """Лічильники клієнта для звіту та вимірювань."""

    mode: str
    attempts: int
    successes: int
    failures: int
    bulkhead_rejections: int
    circuit_rejections: int
    breaker_state: str
    transitions: tuple[BreakerTransition, ...]

    def to_json(self) -> JsonObject:
        """Серіалізує статистику."""
        return {
            "mode": self.mode,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "bulkheadRejections": self.bulkhead_rejections,
            "circuitRejections": self.circuit_rejections,
            "breakerState": self.breaker_state,
            "transitions": [item.to_json() for item in self.transitions],
        }


class MonotonicClock(Protocol):
    """Джерело монотонного часу."""

    def now(self) -> float: ...


class Sleeper(Protocol):
    """Пауза між спробами."""

    def sleep(self, seconds: float) -> None: ...


class Jitter(Protocol):
    """Випадкове розсіювання паузи."""

    def uniform(self, low: float, high: float) -> float: ...


class SystemMonotonicClock:
    """Реальний монотонний годинник."""

    def now(self) -> float:
        return time.monotonic()


class SystemSleeper:
    """Реальна пауза."""

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class RandomJitter:
    """Потокобезпечний генератор рівномірного jitter."""

    def __init__(self, seed: int | None = None) -> None:
        self._random = random.Random(seed)
        self._lock = threading.Lock()

    def uniform(self, low: float, high: float) -> float:
        with self._lock:
            return self._random.uniform(low, high)


class CircuitState(Enum):
    """Три стани аварійного вимикача."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitBreakerConfig:
    """Параметри вимикача."""

    failure_threshold: int = 8
    open_seconds: float = 2.0
    half_open_max_calls: int = 1

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold має бути ≥ 1")
        if self.open_seconds <= 0:
            raise ValueError("open_seconds має бути додатним")
        if self.half_open_max_calls < 1:
            raise ValueError("half_open_max_calls має бути ≥ 1")


class CircuitBreaker:
    """Аварійний вимикач із трьома станами."""

    def __init__(
        self,
        config: CircuitBreakerConfig,
        clock: MonotonicClock,
    ) -> None:
        self._config = config
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_calls = 0
        self._transitions: list[BreakerTransition] = []

    @property
    def state_name(self) -> str:
        """Назва поточного стану."""
        with self._lock:
            return self._state.value

    def transitions(self) -> tuple[BreakerTransition, ...]:
        """Копія журналу переходів."""
        with self._lock:
            return tuple(self._transitions)

    def before_call(self) -> None:
        """Перевіряє, чи можна зробити виклик."""
        with self._lock:
            if self._state is CircuitState.OPEN:
                opened_at = self._opened_at
                if opened_at is not None and (
                    self._clock.now() - opened_at >= self._config.open_seconds
                ):
                    self._set_state(CircuitState.HALF_OPEN, "open_timeout")
                    self._half_open_calls = 1
                    return
                raise CircuitOpenError("аварійний вимикач розімкнений")
            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_calls >= self._config.half_open_max_calls:
                    raise CircuitOpenError("очікується пробний виклик")
                self._half_open_calls += 1

    def on_success(self) -> None:
        """Фіксує успішний виклик."""
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._failures = 0
                self._half_open_calls = 0
                self._set_state(CircuitState.CLOSED, "probe_success")
            elif self._state is CircuitState.CLOSED:
                self._failures = 0

    def on_failure(self) -> None:
        """Фіксує збій і, за потреби, розмикає коло."""
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._open("probe_failure")
            elif self._state is CircuitState.CLOSED:
                self._failures += 1
                if self._failures >= self._config.failure_threshold:
                    self._open("failure_threshold")

    def reset(self) -> None:
        """Повертає вимикач у Closed і очищає історію (для експерименту)."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = None
            self._half_open_calls = 0
            self._transitions.clear()

    def _open(self, cause: str) -> None:
        self._failures = 0
        self._half_open_calls = 0
        self._opened_at = self._clock.now()
        self._set_state(CircuitState.OPEN, cause)

    def _set_state(self, state: CircuitState, cause: str) -> None:
        if state is self._state:
            return
        previous = self._state
        self._state = state
        self._transitions.append(
            BreakerTransition(
                from_state=previous.value,
                to_state=state.value,
                at=self._clock.now(),
                cause=cause,
            )
        )
        if len(self._transitions) > 200:
            del self._transitions[: len(self._transitions) - 200]


class Bulkhead:
    """Семафор, що обмежує одночасні виклики до залежності."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("ліміт Bulkhead має бути ≥ 1")
        self._limit = limit
        self._semaphore = threading.BoundedSemaphore(limit)

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Захоплює слот або одразу відмовляє."""
        if not self._semaphore.acquire(blocking=False):
            raise BulkheadFullError(
                f"перевищено ліміт одночасних викликів: {self._limit}"
            )
        try:
            yield
        finally:
            self._semaphore.release()


@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Параметри повторів з експоненційним відкатом."""

    max_attempts: int = 4
    base_delay: float = 0.1
    factor: float = 2.0
    max_delay: float = 0.8
    jitter_fraction: float = 0.5

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts має бути ≥ 1")
        if self.base_delay <= 0:
            raise ValueError("base_delay має бути додатним")
        if self.factor < 1:
            raise ValueError("factor має бути ≥ 1")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay не може бути меншим за base_delay")
        if not 0.0 <= self.jitter_fraction <= 1.0:
            raise ValueError("jitter_fraction має бути в межах 0..1")


class RetryPolicy:
    """Повторює лише тимчасові помилки."""

    def __init__(self, config: RetryConfig) -> None:
        self._config = config

    @property
    def config(self) -> RetryConfig:
        """Параметри для звіту."""
        return self._config

    def delay_for(self, failed_attempt: int, jitter: Jitter) -> float:
        """Пауза після невдалої спроби з рівномірним jitter."""
        if failed_attempt < 1:
            raise ValueError("failed_attempt має бути ≥ 1")
        raw = min(
            self._config.max_delay,
            self._config.base_delay * self._config.factor ** (failed_attempt - 1),
        )
        spread = raw * self._config.jitter_fraction
        floor = raw - spread
        return floor + jitter.uniform(0.0, spread)

    def call(
        self,
        operation: Callable[[], T],
        sleeper: Sleeper,
        jitter: Jitter,
    ) -> tuple[T, int]:
        """Виконує операцію, роблячи паузи між спробами."""
        attempt = 0
        while True:
            attempt += 1
            try:
                return operation(), attempt
            except RetryablePaymentError:
                if attempt >= self._config.max_attempts:
                    raise
                sleeper.sleep(self.delay_for(attempt, jitter))


class TimeoutGuard:
    """Перевіряє, що виклик уклався в ліміт часу.

    Основний тайм-аут передається в HTTP-клієнт (urlopen), а цей
    охоронець додатково перевіряє фактичну тривалість після повернення.
    """

    def __init__(self, seconds: float, clock: MonotonicClock) -> None:
        if seconds <= 0:
            raise ValueError("timeout має бути додатним")
        self._seconds = seconds
        self._clock = clock

    def call(self, operation: Callable[[], T]) -> T:
        """Виконує операцію й перевіряє час."""
        started = self._clock.now()
        result = operation()
        elapsed = self._clock.now() - started
        if elapsed > self._seconds:
            raise TimeoutPaymentError(
                f"виклик тривав {elapsed:.3f} с, ліміт {self._seconds:.3f} с"
            )
        return result


class PaymentTransport(Protocol):
    """Транспорт до платіжного шлюзу."""

    def send(self, request: PaymentRequest, timeout: float) -> PaymentResponse: ...


class HttpPaymentTransport:
    """HTTP/JSON транспорт на стандартній бібліотеці."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def send(self, request: PaymentRequest, timeout: float) -> PaymentResponse:
        body = json.dumps(
            {
                "orderId": request.order_id,
                "amount": f"{request.amount:.2f}",
                "currency": request.currency,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        http_request = urllib.request.Request(
            self._base_url + "/payments",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": request.idempotency_key,
            },
        )
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                status = response.getcode()
                raw = response.read()
        except urllib.error.HTTPError as error:
            raw = error.read()
            message = _error_message(raw)
            if error.code == 409:
                raise IdempotencyConflictError(message) from error
            if error.code in (400, 404, 422):
                raise PaymentDeclinedError(message) from error
            if error.code >= 500:
                raise DependencyUnavailableError(message) from error
            raise TransportPaymentError(f"HTTP {error.code}: {message}") from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise TimeoutPaymentError("тайм-аут виклику") from error
            raise TransportPaymentError(f"мережева помилка: {error.reason}") from error
        except TimeoutError as error:
            raise TimeoutPaymentError("тайм-аут виклику") from error
        except (
            ConnectionResetError,
            http.client.RemoteDisconnected,
            OSError,
        ) as error:
            raise TransportPaymentError(f"з'єднання розірвано: {error}") from error

        if status not in (200, 201):
            raise TransportPaymentError(f"неочікуваний HTTP-статус: {status}")
        payload = _decode(raw)
        amount_text = as_str(field(payload, "amount"), "amount")
        try:
            amount = Decimal(amount_text)
        except InvalidOperation as error:
            raise TransportPaymentError(
                f"некоректна сума у відповіді: {amount_text!r}"
            ) from error
        try:
            return PaymentResponse(
                payment_id=as_str(field(payload, "paymentId"), "paymentId"),
                order_id=as_str(field(payload, "orderId"), "orderId"),
                status=as_str(field(payload, "status"), "status"),
                amount=amount,
                currency=as_str(field(payload, "currency"), "currency"),
                captured_at=datetime_from_json(
                    field(payload, "capturedAt"), "capturedAt"
                ),
                idempotent_replay=as_bool(
                    field(payload, "idempotentReplay"), "idempotentReplay"
                ),
            )
        except ValueError as error:
            raise TransportPaymentError(
                f"некоректна відповідь платіжного сервісу: {error}"
            ) from error


def _decode(raw: bytes) -> JsonObject:
    if not raw:
        raise TransportPaymentError("порожня відповідь платіжного сервісу")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TransportPaymentError("відповідь не є JSON") from error
    try:
        return as_object(value, "response")
    except ValueError as error:
        raise TransportPaymentError(str(error)) from error


def _error_message(raw: bytes) -> str:
    try:
        payload = _decode(raw)
        error = as_object(field(payload, "error"), "error")
        return as_str(field(error, "message"), "message")
    except (PaymentCallError, ValueError):
        return "помилка платіжного сервісу"


def error_code(error: PaymentCallError) -> str:
    """Стабільний код помилки для відповіді ядра."""
    if isinstance(error, IdempotencyConflictError):
        return "idempotency_conflict"
    if isinstance(error, PaymentDeclinedError):
        return "payment_declined"
    if isinstance(error, TimeoutPaymentError):
        return "timeout"
    if isinstance(error, DependencyUnavailableError):
        return "dependency_unavailable"
    if isinstance(error, TransportPaymentError):
        return "transport_error"
    if isinstance(error, CircuitOpenError):
        return "circuit_open"
    if isinstance(error, BulkheadFullError):
        return "bulkhead_full"
    return "payment_error"


class PaymentClient(Protocol):
    """Спільний інтерфейс наївного та захищеного клієнта."""

    def capture(self, request: PaymentRequest) -> PaymentCallResult: ...

    def stats(self) -> ClientStats: ...

    def reset(self) -> None: ...


class NaivePaymentClient:
    """Клієнт без повторів, вимикача й Bulkhead; лише тайм-аут."""

    def __init__(self, transport: PaymentTransport, timeout: float) -> None:
        self._transport = transport
        self._timeout = timeout
        self._lock = threading.Lock()
        self._attempts = 0
        self._successes = 0
        self._failures = 0

    def capture(self, request: PaymentRequest) -> PaymentCallResult:
        if not request.idempotency_key.strip():
            return PaymentCallResult.failure(
                "idempotency_required",
                "для повторів потрібен ключ ідемпотентності",
                0,
            )
        try:
            response = self._transport.send(request, self._timeout)
        except PaymentCallError as error:
            with self._lock:
                self._attempts += 1
                self._failures += 1
            return PaymentCallResult.failure(error_code(error), str(error), 1)
        with self._lock:
            self._attempts += 1
            self._successes += 1
        return PaymentCallResult.success(response, 1)

    def stats(self) -> ClientStats:
        with self._lock:
            return ClientStats(
                mode="naive",
                attempts=self._attempts,
                successes=self._successes,
                failures=self._failures,
                bulkhead_rejections=0,
                circuit_rejections=0,
                breaker_state="disabled",
                transitions=(),
            )

    def reset(self) -> None:
        with self._lock:
            self._attempts = 0
            self._successes = 0
            self._failures = 0


class ProtectedPaymentClient:
    """Клієнт із Bulkhead, Retry, Circuit Breaker і Timeout."""

    def __init__(
        self,
        transport: PaymentTransport,
        timeout: float,
        retry: RetryPolicy,
        breaker: CircuitBreaker,
        bulkhead: Bulkhead,
        sleeper: Sleeper,
        jitter: Jitter,
        clock: MonotonicClock,
    ) -> None:
        self._transport = transport
        self._timeout = timeout
        self._retry = retry
        self._breaker = breaker
        self._bulkhead = bulkhead
        self._sleeper = sleeper
        self._jitter = jitter
        self._timeout_guard = TimeoutGuard(timeout, clock)
        self._lock = threading.Lock()
        self._attempts = 0
        self._successes = 0
        self._failures = 0
        self._bulkhead_rejections = 0
        self._circuit_rejections = 0

    def capture(self, request: PaymentRequest) -> PaymentCallResult:
        if not request.idempotency_key.strip():
            return PaymentCallResult.failure(
                "idempotency_required",
                "для повторів потрібен ключ ідемпотентності",
                0,
            )

        attempts = 0
        try:
            with self._bulkhead.slot():
                def attempt() -> PaymentResponse:
                    nonlocal attempts
                    self._breaker.before_call()
                    attempts += 1
                    try:
                        response = self._timeout_guard.call(
                            lambda: self._transport.send(request, self._timeout)
                        )
                    except RetryablePaymentError:
                        with self._lock:
                            self._failures += 1
                        self._breaker.on_failure()
                        raise
                    with self._lock:
                        self._successes += 1
                    self._breaker.on_success()
                    return response

                response, _ = self._retry.call(attempt, self._sleeper, self._jitter)
        except BulkheadFullError as error:
            with self._lock:
                self._bulkhead_rejections += 1
            return PaymentCallResult.failure(
                "bulkhead_full", str(error), attempts
            )
        except CircuitOpenError as error:
            with self._lock:
                self._circuit_rejections += 1
            return PaymentCallResult.failure(
                "circuit_open", str(error), attempts
            )
        except PaymentCallError as error:
            return PaymentCallResult.failure(error_code(error), str(error), attempts)
        finally:
            with self._lock:
                self._attempts += attempts

        return PaymentCallResult.success(response, attempts)

    def stats(self) -> ClientStats:
        with self._lock:
            return ClientStats(
                mode="protected",
                attempts=self._attempts,
                successes=self._successes,
                failures=self._failures,
                bulkhead_rejections=self._bulkhead_rejections,
                circuit_rejections=self._circuit_rejections,
                breaker_state=self._breaker.state_name,
                transitions=self._breaker.transitions(),
            )

    def reset(self) -> None:
        with self._lock:
            self._attempts = 0
            self._successes = 0
            self._failures = 0
            self._bulkhead_rejections = 0
            self._circuit_rejections = 0
        self._breaker.reset()
