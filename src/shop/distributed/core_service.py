"""Сервіс «Ядро»: команди, журнал подій, проєкція та захист платежів.

Ядро приймає HTTP/JSON-команди, змінює агрегат лише через журнал,
читає стан із журналу та список замовлень із проєкції. Виклик
платіжного шлюзу захищений Timeout, Retry, Circuit Breaker і Bulkhead;
під час збою команда повертає `degraded`, а сервіс продовжує працювати.
"""

from __future__ import annotations

import argparse
import os
import threading
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shop.domain.errors import (
    DomainError,
    InvalidState,
    describe_error,
)
from shop.domain.ids import OrderId
from shop.domain.payment import PaymentReceipt
from shop.domain.result import Err
from shop.infrastructure.clock import SystemClock

from .event_store import EventStore, VersionConflict
from .jsonutil import (
    JsonObject,
    as_int,
    as_list,
    as_str,
    field,
    optional_field,
    read_json_body,
    send_error,
    send_json,
    split_path,
)
from .model import (
    CancelOrder,
    OrderCommand,
    OrderState,
    PayOrder,
    PlaceOrder,
    customer_id_from_text,
    decide,
    line_from_json,
    order_id_from_text,
    state_to_json,
)
from .projection import Projection, ProjectionStore, Projector
from .resilience import (
    Bulkhead,
    CircuitBreaker,
    CircuitBreakerConfig,
    HttpPaymentTransport,
    NaivePaymentClient,
    PaymentCallResult,
    PaymentClient,
    PaymentRequest,
    ProtectedPaymentClient,
    RandomJitter,
    RetryConfig,
    RetryPolicy,
    SystemMonotonicClock,
    SystemSleeper,
)


@dataclass(frozen=True, slots=True)
class CoreConfig:
    """Параметри сервісу «Ядро»."""

    host: str = "127.0.0.1"
    port: int = 8080
    event_log: Path = Path("var/lr3/events.jsonl")
    checkpoint: Path = Path("var/lr3/projection.json")
    payment_url: str = "http://127.0.0.1:8081"
    client_mode: str = "protected"
    request_timeout: float = 3.0
    attempt_timeout: float = 0.25
    retry_attempts: int = 4
    retry_base_delay: float = 0.1
    retry_max_delay: float = 0.8
    breaker_threshold: int = 8
    breaker_open_seconds: float = 2.0
    bulkhead_limit: int = 8
    projection_poll_seconds: float = 0.01
    jitter_seed: int | None = None

    def __post_init__(self) -> None:
        if self.client_mode not in ("naive", "protected"):
            raise ValueError("client_mode має бути naive або protected")
        if self.request_timeout <= 0 or self.attempt_timeout <= 0:
            raise ValueError("тайм-аути мають бути додатними")
        if self.retry_attempts < 1:
            raise ValueError("retry_attempts має бути ≥ 1")
        if self.retry_base_delay <= 0 or self.retry_max_delay < self.retry_base_delay:
            raise ValueError("некоректні параметри Retry")
        if self.breaker_threshold < 1 or self.breaker_open_seconds <= 0:
            raise ValueError("некоректні параметри Circuit Breaker")
        if self.bulkhead_limit < 1:
            raise ValueError("bulkhead_limit має бути ≥ 1")
        if self.projection_poll_seconds <= 0:
            raise ValueError("projection_poll_seconds має бути додатним")

    @classmethod
    def from_env(cls) -> CoreConfig:
        """Читає параметри зі змінних середовища."""
        seed_raw = os.environ.get("SHOP_JITTER_SEED")
        return cls(
            host=os.environ.get("SHOP_CORE_HOST", "127.0.0.1"),
            port=_env_int("SHOP_CORE_PORT", 8080),
            event_log=Path(
                os.environ.get("SHOP_EVENT_LOG", "var/lr3/events.jsonl")
            ),
            checkpoint=Path(
                os.environ.get("SHOP_CHECKPOINT", "var/lr3/projection.json")
            ),
            payment_url=os.environ.get(
                "SHOP_PAYMENT_URL", "http://127.0.0.1:8081"
            ),
            client_mode=os.environ.get("SHOP_PAYMENT_CLIENT", "protected"),
            request_timeout=_env_float("SHOP_REQUEST_TIMEOUT", 3.0),
            attempt_timeout=_env_float("SHOP_ATTEMPT_TIMEOUT", 0.25),
            retry_attempts=_env_int("SHOP_RETRY_ATTEMPTS", 4),
            retry_base_delay=_env_float("SHOP_RETRY_BASE_DELAY", 0.1),
            retry_max_delay=_env_float("SHOP_RETRY_MAX_DELAY", 0.8),
            breaker_threshold=_env_int("SHOP_BREAKER_THRESHOLD", 8),
            breaker_open_seconds=_env_float("SHOP_BREAKER_OPEN_SECONDS", 2.0),
            bulkhead_limit=_env_int("SHOP_BULKHEAD_LIMIT", 8),
            projection_poll_seconds=_env_float(
                "SHOP_PROJECTION_POLL_SECONDS", 0.01
            ),
            jitter_seed=None if seed_raw is None else int(seed_raw),
        )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class CoreMetrics:
    """Лічильники ядра для звіту."""

    commands_total: int = 0
    commands_applied: int = 0
    commands_duplicate: int = 0
    commands_degraded: int = 0
    commands_rejected: int = 0
    payment_requests: int = 0
    payment_success: int = 0
    payment_failure: int = 0

    def to_json(self) -> JsonObject:
        """Серіалізує лічильники."""
        return {
            "commandsTotal": self.commands_total,
            "commandsApplied": self.commands_applied,
            "commandsDuplicate": self.commands_duplicate,
            "commandsDegraded": self.commands_degraded,
            "commandsRejected": self.commands_rejected,
            "paymentRequests": self.payment_requests,
            "paymentSuccess": self.payment_success,
            "paymentFailure": self.payment_failure,
        }


class CoreService:
    """Застосунковий шар сервісу «Ядро»."""

    def __init__(self, config: CoreConfig) -> None:
        self._config = config
        self._store = EventStore(config.event_log)
        self._projection = Projection()
        self._snapshot_store = ProjectionStore(config.checkpoint)
        snapshot = self._snapshot_store.load()
        if snapshot is not None and snapshot.checkpoint <= self._store.head():
            self._projection.load(snapshot)
        else:
            self._projection.rebuild(self._store.read_all())
            self._snapshot_store.save(self._projection.snapshot())
        self._projector = Projector(
            store=self._store,
            projection=self._projection,
            snapshot_store=self._snapshot_store,
            poll_interval=config.projection_poll_seconds,
        )
        self._client = _build_payment_client(config)
        self._clock = SystemClock()
        self._metrics = CoreMetrics()
        self._metrics_lock = threading.Lock()
        self._locks_guard = threading.Lock()
        self._order_locks: dict[str, threading.Lock] = {}
        self._projector.start()

    def close(self) -> None:
        """Зупиняє фоновий проєктор."""
        self._projector.stop()

    @property
    def client_mode(self) -> str:
        """Режим клієнта для /health."""
        return self._config.client_mode

    def handle_command(self, body: JsonObject) -> tuple[int, JsonObject]:
        """Обробляє POST /commands."""
        with self._metrics_lock:
            self._metrics.commands_total += 1
        try:
            command_id = as_str(field(body, "commandId"), "commandId")
            command_type = as_str(field(body, "type"), "type")
            order_id = order_id_from_text(
                as_str(field(body, "orderId"), "orderId")
            )
            expected_raw = optional_field(body, "expectedVersion")
            expected_version = (
                None if expected_raw is None else as_int(expected_raw, "expectedVersion")
            )
        except ValueError as error:
            return self._rejected(400, "invalid_request", str(error))

        existing = self._store.find_command(command_id)
        if existing is not None:
            if not existing or existing[0].stream_id != str(order_id):
                return self._rejected(
                    409,
                    "command_id_conflict",
                    "commandId уже використано для іншого замовлення",
                )
            state = self._store.rebuild_state(str(order_id))
            with self._metrics_lock:
                self._metrics.commands_duplicate += 1
            return 200, self._command_body(
                "duplicate",
                command_id,
                state,
                [stored.event_type for stored in existing],
            )

        try:
            match command_type:
                case "PlaceOrder":
                    customer_id = customer_id_from_text(
                        as_str(field(body, "customerId"), "customerId")
                    )
                    lines = tuple(
                        line_from_json(item)
                        for item in as_list(field(body, "lines"), "lines")
                    )
                    command: OrderCommand = PlaceOrder(
                        command_id=command_id,
                        order_id=order_id,
                        customer_id=customer_id,
                        lines=lines,
                        at=self._clock.now(),
                    )
                    return self._execute(
                        command, expected_version, order_id
                    )
                case "PayOrder":
                    pay_command = PayOrder(
                        command_id=command_id,
                        order_id=order_id,
                        at=self._clock.now(),
                    )
                    return self._execute_pay(pay_command, expected_version)
                case "CancelOrder":
                    cancel_command = CancelOrder(
                        command_id=command_id,
                        order_id=order_id,
                        reason=as_str(field(body, "reason"), "reason"),
                        at=self._clock.now(),
                    )
                    return self._execute(cancel_command, expected_version, order_id)
                case _:
                    return self._rejected(
                        400,
                        "unknown_command",
                        f"невідомий тип команди: {command_type!r}",
                    )
        except ValueError as error:
            return self._rejected(400, "invalid_request", str(error))

    def _execute(
        self,
        command: OrderCommand,
        expected_version: int | None,
        order_id: OrderId,
    ) -> tuple[int, JsonObject]:
        stream = str(order_id)
        with self._lock_for(stream):
            for _ in range(3):
                state = self._store.rebuild_state(stream)
                decision = decide(state, command)
                if isinstance(decision, Err):
                    return self._domain_error(decision.error)
                version = state.version if expected_version is None else expected_version
                try:
                    result = self._store.append(
                        command_id=command.command_id,
                        stream_id=stream,
                        expected_version=version,
                        events=decision.value,
                    )
                except VersionConflict as error:
                    if expected_version is not None:
                        return self._rejected(
                            409, "version_conflict", str(error)
                        )
                    continue
                if result.duplicate:
                    with self._metrics_lock:
                        self._metrics.commands_duplicate += 1
                    return 200, self._command_body(
                        "duplicate",
                        command.command_id,
                        self._store.rebuild_state(stream),
                        [stored.event_type for stored in result.stored],
                    )
                with self._metrics_lock:
                    self._metrics.commands_applied += 1
                return 200, self._command_body(
                    "applied",
                    command.command_id,
                    self._store.rebuild_state(stream),
                    [stored.event_type for stored in result.stored],
                )
            return self._rejected(
                409,
                "version_conflict",
                "не вдалося записати події після кількох спроб",
            )

    def _execute_pay(
        self,
        command: PayOrder,
        expected_version: int | None,
    ) -> tuple[int, JsonObject]:
        stream = str(command.order_id)
        with self._lock_for(stream):
            state = self._store.rebuild_state(stream)
            if state.status != "placed":
                return self._rejected(
                    409,
                    "invalid_state",
                    f"оплата можлива лише зі стану placed, фактичний — {state.status}",
                )
            if state.total is None:
                return self._rejected(
                    500, "internal_error", "у замовленні немає суми"
                )

            request = PaymentRequest(
                order_id=stream,
                amount=state.total.amount,
                currency=state.total.currency.value,
                idempotency_key=f"order:{stream}:payment",
            )
            with self._metrics_lock:
                self._metrics.payment_requests += 1
            result = self._client.capture(request)
            if not result.ok or result.response is None:
                with self._metrics_lock:
                    self._metrics.payment_failure += 1
                    self._metrics.commands_degraded += 1
                return 200, self._degraded_body(command.command_id, state, result)

            with self._metrics_lock:
                self._metrics.payment_success += 1
            receipt = PaymentReceipt(
                transaction_id=result.response.payment_id,
                amount=state.total,
                charged_at=self._clock.now(),
            )
            paid = replace(command, receipt=receipt)
            return self._append_paid(paid, expected_version)

    def _append_paid(
        self,
        command: PayOrder,
        expected_version: int | None,
    ) -> tuple[int, JsonObject]:
        stream = str(command.order_id)
        for _ in range(3):
            state = self._store.rebuild_state(stream)
            decision = decide(state, command)
            if isinstance(decision, Err):
                if state.status == "paid":
                    with self._metrics_lock:
                        self._metrics.commands_duplicate += 1
                    return 200, self._command_body(
                        "duplicate", command.command_id, state, []
                    )
                return self._domain_error(decision.error)
            version = state.version if expected_version is None else expected_version
            try:
                result = self._store.append(
                    command_id=command.command_id,
                    stream_id=stream,
                    expected_version=version,
                    events=decision.value,
                )
            except VersionConflict as error:
                if expected_version is not None:
                    return self._rejected(409, "version_conflict", str(error))
                continue
            with self._metrics_lock:
                self._metrics.commands_applied += 1
            return 200, self._command_body(
                "applied",
                command.command_id,
                self._store.rebuild_state(stream),
                [stored.event_type for stored in result.stored],
            )
        return self._rejected(
            409,
            "version_conflict",
            "платіж отримано, але подію не вдалося записати; повторіть команду",
        )

    def _lock_for(self, stream: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._order_locks.get(stream)
            if lock is None:
                lock = threading.Lock()
                self._order_locks[stream] = lock
            return lock

    def _domain_error(self, error: DomainError) -> tuple[int, JsonObject]:
        status = 409 if isinstance(error, InvalidState) else 400
        code = "invalid_state" if isinstance(error, InvalidState) else "domain_error"
        return self._rejected(status, code, describe_error(error))

    def _rejected(
        self,
        status: int,
        code: str,
        message: str,
    ) -> tuple[int, JsonObject]:
        with self._metrics_lock:
            self._metrics.commands_rejected += 1
        return status, {"error": {"code": code, "message": message}}

    def _command_body(
        self,
        status: str,
        command_id: str,
        state: OrderState,
        events: list[str],
    ) -> JsonObject:
        return {
            "commandId": command_id,
            "status": status,
            "orderId": str(state.order_id),
            "orderStatus": state.status,
            "version": state.version,
            "events": events,
            "degraded": False,
        }

    def _degraded_body(
        self,
        command_id: str,
        state: OrderState,
        result: PaymentCallResult,
    ) -> JsonObject:
        code = result.error_code or "payment_error"
        message = result.error_message or "платіж недоступний"
        return {
            "commandId": command_id,
            "status": "degraded",
            "orderId": str(state.order_id),
            "orderStatus": state.status,
            "version": state.version,
            "events": [],
            "degraded": True,
            "fallback": "payment_deferred",
            "error": {"code": code, "message": message},
        }

    def get_order(self, order_id_text: str) -> tuple[int, JsonObject]:
        """Читає стан агрегату з журналу."""
        try:
            order_id = order_id_from_text(order_id_text)
        except ValueError as error:
            return 400, {"error": {"code": "invalid_request", "message": str(error)}}
        state = self._store.rebuild_state(str(order_id))
        if state.status == "new":
            return 404, {
                "error": {
                    "code": "order_not_found",
                    "message": f"замовлення {order_id} не знайдено",
                }
            }
        return 200, state_to_json(state)

    def projection_orders(self, status: str | None) -> tuple[int, JsonObject]:
        """Читає список замовлень із проєкції."""
        if status is not None and status not in ("placed", "paid", "cancelled"):
            return 400, {
                "error": {
                    "code": "invalid_status",
                    "message": f"невідомий статус: {status!r}",
                }
            }
        orders = self._projector.orders(status)
        lag = self._projector.lag()
        return 200, {
            "orders": [order.to_json() for order in orders],
            "checkpoint": lag.checkpoint,
            "head": lag.head,
        }

    def projection_order(self, order_id: str) -> tuple[int, JsonObject]:
        """Читає одне замовлення з проєкції."""
        order = self._projector.order(order_id)
        if order is None:
            return 404, {
                "error": {
                    "code": "order_not_found",
                    "message": f"замовлення {order_id} відсутнє в проєкції",
                }
            }
        return 200, order.to_json()

    def projection_lag(self) -> tuple[int, JsonObject]:
        """Повертає відставання проєкції."""
        return 200, self._projector.lag().to_json()

    def replay_projection(self) -> tuple[int, JsonObject]:
        """Переграє журнал і повертає контрольну суму проєкції."""
        self._projector.rebuild()
        snapshot = self._projection.snapshot()
        return 200, {
            "checkpoint": snapshot.checkpoint,
            "orders": len(snapshot.orders),
            "checksum": self._projector.checksum(),
        }

    def stats(self) -> tuple[int, JsonObject]:
        """Стан для експерименту."""
        with self._metrics_lock:
            metrics = self._metrics.to_json()
        return 200, {
            "metrics": metrics,
            "paymentClient": self._client.stats().to_json(),
            "projection": self._projector.lag().to_json(),
            "config": {
                "clientMode": self._config.client_mode,
                "requestTimeout": self._config.request_timeout,
                "attemptTimeout": self._config.attempt_timeout,
                "retryAttempts": self._config.retry_attempts,
                "breakerThreshold": self._config.breaker_threshold,
                "breakerOpenSeconds": self._config.breaker_open_seconds,
                "bulkheadLimit": self._config.bulkhead_limit,
            },
        }

    def reset_admin(self) -> tuple[int, JsonObject]:
        """Скидає лічильники та вимикач (лише для експерименту)."""
        with self._metrics_lock:
            self._metrics = CoreMetrics()
        self._client.reset()
        return self.stats()


def _build_payment_client(config: CoreConfig) -> PaymentClient:
    transport = HttpPaymentTransport(config.payment_url)
    if config.client_mode == "naive":
        return NaivePaymentClient(transport, config.request_timeout)
    clock = SystemMonotonicClock()
    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            failure_threshold=config.breaker_threshold,
            open_seconds=config.breaker_open_seconds,
        ),
        clock,
    )
    retry = RetryPolicy(
        RetryConfig(
            max_attempts=config.retry_attempts,
            base_delay=config.retry_base_delay,
            max_delay=config.retry_max_delay,
        )
    )
    bulkhead = Bulkhead(config.bulkhead_limit)
    return ProtectedPaymentClient(
        transport=transport,
        timeout=config.attempt_timeout,
        retry=retry,
        breaker=breaker,
        bulkhead=bulkhead,
        sleeper=SystemSleeper(),
        jitter=RandomJitter(config.jitter_seed),
        clock=clock,
    )


def make_server(config: CoreConfig) -> tuple[ThreadingHTTPServer, CoreService]:
    """Створює HTTP-сервер ядра; port=0 вибирає вільний порт."""
    service = CoreService(config)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            path, query = split_path(self.path)
            if path == "/health":
                send_json(
                    self,
                    200,
                    {
                        "status": "ok",
                        "clientMode": service.client_mode,
                    },
                )
                return
            if path == "/stats":
                status, payload = service.stats()
                send_json(self, status, payload)
                return
            if path == "/projection/orders":
                status, payload = service.projection_orders(query.get("status"))
                send_json(self, status, payload)
                return
            if path == "/projection/lag":
                status, payload = service.projection_lag()
                send_json(self, status, payload)
                return
            if path.startswith("/projection/orders/"):
                order_id = path[len("/projection/orders/") :]
                status, payload = service.projection_order(order_id)
                send_json(self, status, payload)
                return
            if path.startswith("/orders/"):
                order_id = path[len("/orders/") :]
                status, payload = service.get_order(order_id)
                send_json(self, status, payload)
                return
            send_error(self, 404, "not_found", f"невідомий шлях {path}")

        def do_POST(self) -> None:
            path, _query = split_path(self.path)
            try:
                if path == "/commands":
                    body = read_json_body(self)
                    status, payload = service.handle_command(body)
                    send_json(self, status, payload)
                    return
                if path == "/projection/replay":
                    status, payload = service.replay_projection()
                    send_json(self, status, payload)
                    return
                if path == "/admin/reset":
                    status, payload = service.reset_admin()
                    send_json(self, status, payload)
                    return
                send_error(self, 404, "not_found", f"невідомий шлях {path}")
            except ValueError as error:
                send_error(self, 400, "invalid_request", str(error))
            except Exception as error:  # pragma: no cover - захист від непередбаченого
                send_error(self, 500, "internal_error", str(error))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = Server((config.host, config.port), Handler)
    return server, service


def main() -> int:
    """Запуск окремим процесом."""
    config = CoreConfig.from_env()
    parser = argparse.ArgumentParser(description="Ядро інтернет-магазину (ЛР3)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--event-log", default=None)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--payment-url", default=None)
    parser.add_argument("--client", choices=("naive", "protected"), default=None)
    args = parser.parse_args()
    if args.host is not None:
        config = replace(config, host=args.host)
    if args.port is not None:
        config = replace(config, port=args.port)
    if args.event_log is not None:
        config = replace(config, event_log=Path(args.event_log))
    if args.checkpoint is not None:
        config = replace(config, checkpoint=Path(args.checkpoint))
    if args.payment_url is not None:
        config = replace(config, payment_url=args.payment_url)
    if args.client is not None:
        config = replace(config, client_mode=args.client)

    server, service = make_server(config)
    print(
        f"core service: http://{config.host}:{server.server_port} "
        f"(client={config.client_mode}, payment={config.payment_url})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
