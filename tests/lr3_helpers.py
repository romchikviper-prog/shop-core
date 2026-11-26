"""Спільні допоміжні засоби для тестів ЛР3."""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from http.server import ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from shop.distributed.core_service import CoreConfig, CoreService, make_server as make_core
from shop.distributed.model import line_to_json
from shop.distributed.payment_service import (
    PaymentServiceConfig,
    make_server as make_payment,
)
from shop.distributed.resilience import (
    DependencyUnavailableError,
    PaymentResponse,
)
from shop.domain.ids import CustomerId, OrderId, Sku
from shop.domain.money import Currency, Money
from shop.domain.order import OrderLine


@dataclass
class RunningServer:
    """Запущений у потоці HTTP-сервер для тесту."""

    server: ThreadingHTTPServer
    service: Any
    base_url: str
    thread: threading.Thread

    def close(self) -> None:
        if isinstance(self.service, CoreService):
            self.service.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)


def start_payment(config: PaymentServiceConfig) -> RunningServer:
    server, service = make_payment(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return RunningServer(
        server=server,
        service=service,
        base_url=f"http://{config.host}:{server.server_port}",
        thread=thread,
    )


def start_core(config: CoreConfig) -> RunningServer:
    server, service = make_core(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return RunningServer(
        server=server,
        service=service,
        base_url=f"http://{config.host}:{server.server_port}",
        thread=thread,
    )


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    merged = {"Content-Type": "application/json"}
    if headers is not None:
        merged.update(headers)
    request = Request(url, data=data, method=method, headers=merged)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw)
    except HTTPError as error:
        raw = error.read().decode("utf-8")
        return error.code, json.loads(raw) if raw else {}


def utc_now() -> datetime:
    return datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def new_order_id() -> OrderId:
    return OrderId(uuid4())


def new_customer_id() -> CustomerId:
    return CustomerId(uuid4())


def order_line(quantity: int = 1) -> OrderLine:
    return OrderLine(
        sku=Sku("PHN-0001"),
        name="Смартфон Galaxy X",
        unit_price=Money(Decimal("19999.00"), Currency.UAH),
        quantity=quantity,
    )


def order_line_json(quantity: int = 1) -> dict[str, Any]:
    return line_to_json(order_line(quantity))


def success_response(request: Any) -> PaymentResponse:
    return PaymentResponse(
        payment_id="pay-000001",
        order_id=request.order_id,
        status="captured",
        amount=request.amount,
        currency=request.currency,
        captured_at=utc_now(),
        idempotent_replay=False,
    )


class FakeClock:
    """Керований монотонний годинник."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class RecordingSleeper:
    """Записує паузи, не сплячи."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)


class FixedJitter:
    """Детермінований jitter: value=0 — нижня межа, 1 — верхня."""

    def __init__(self, value: float = 0.0) -> None:
        self._value = value

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self._value


class ScriptedTransport:
    """Транспорт із наперед заданою послідовністю відповідей."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def send(self, request: Any, timeout: float) -> PaymentResponse:
        self.calls += 1
        if not self._outcomes:
            raise DependencyUnavailableError("сценарій вичерпано")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return success_response(request)
