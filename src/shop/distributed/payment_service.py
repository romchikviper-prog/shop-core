"""Залежний сервіс «платіжний шлюз» із перемикачем збоїв.

Сервіс реально працює по HTTP і зберігає платежі в пам'яті за ключем
ідемпотентності. Режими `normal`, `slow`, `errors`, `unavailable`
дозволяють повторити chaos-експеримент без Toxiproxy: затримка,
частка 503 і розрив з'єднання вбудовані в сам сервіс.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Literal, TypeAlias
from uuid import UUID

from .jsonutil import (
    JsonObject,
    as_str,
    datetime_to_json,
    field,
    read_json_body,
    send_error,
    send_json,
    split_path,
)

PaymentMode: TypeAlias = Literal["normal", "slow", "errors", "unavailable"]
VALID_MODES: tuple[PaymentMode, ...] = ("normal", "slow", "errors", "unavailable")
_IDEMPOTENCY_PATTERN = re.compile(r"^order:[0-9a-fA-F-]{36}:payment$")


def parse_mode(value: str) -> PaymentMode:
    """Перевіряє назву режиму."""
    for mode in VALID_MODES:
        if value == mode:
            return mode
    raise ValueError(
        f"невідомий режим {value!r}; допустимі: {', '.join(VALID_MODES)}"
    )


@dataclass(frozen=True, slots=True)
class PaymentServiceConfig:
    """Параметри залежного сервісу."""

    host: str = "127.0.0.1"
    port: int = 8081
    delay_seconds: float = 0.08
    slow_delay_seconds: float = 1.0
    error_fraction: float = 0.5
    seed: int = 42

    def __post_init__(self) -> None:
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds не може бути від'ємним")
        if self.slow_delay_seconds < 0:
            raise ValueError("slow_delay_seconds не може бути від'ємним")
        if not 0.0 <= self.error_fraction <= 1.0:
            raise ValueError("error_fraction має бути в межах 0..1")

    @classmethod
    def from_env(cls) -> PaymentServiceConfig:
        """Читає параметри зі змінних середовища."""
        return cls(
            host=os.environ.get("SHOP_PAYMENT_HOST", "127.0.0.1"),
            port=_env_int("SHOP_PAYMENT_PORT", 8081),
            delay_seconds=_env_float("SHOP_PAYMENT_DELAY", 0.08),
            slow_delay_seconds=_env_float("SHOP_PAYMENT_SLOW_DELAY", 1.0),
            error_fraction=_env_float("SHOP_PAYMENT_ERROR_FRACTION", 0.5),
            seed=_env_int("SHOP_PAYMENT_SEED", 42),
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
class PaymentStats:
    """Лічильники запитів до залежного сервісу."""

    total: int = 0
    accepted: int = 0
    replays: int = 0
    errors: int = 0
    unavailable: int = 0
    invalid: int = 0
    conflicts: int = 0

    def to_json(self) -> JsonObject:
        """Серіалізує лічильники."""
        return {
            "total": self.total,
            "accepted": self.accepted,
            "replays": self.replays,
            "errors": self.errors,
            "unavailable": self.unavailable,
            "invalid": self.invalid,
            "conflicts": self.conflicts,
        }


@dataclass(frozen=True, slots=True)
class PaymentHttpResult:
    """Результат обробки HTTP-запиту; body=None означає розрив з'єднання."""

    status: int
    body: JsonObject | None


@dataclass
class _Capture:
    fingerprint: str
    response: JsonObject


class PaymentService:
    """Ідемпотентний платіжний шлюз із керованими збоями."""

    def __init__(self, config: PaymentServiceConfig) -> None:
        self._config = config
        self._lock = threading.RLock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._captures: dict[str, _Capture] = {}
        self._stats = PaymentStats()
        self._mode: PaymentMode = "normal"
        self._next_payment = 1
        self._random = random.Random(config.seed)

    @property
    def mode(self) -> PaymentMode:
        """Поточний режим збоїв."""
        with self._lock:
            return self._mode

    def set_mode(self, value: str) -> JsonObject:
        """Перемикає режим і повертає стан сервісу."""
        mode = parse_mode(value)
        with self._lock:
            self._mode = mode
        return self.state()

    def reset_stats(self) -> JsonObject:
        """Обнуляє лічильники, не чіпаючи збережених платежів."""
        with self._lock:
            self._stats = PaymentStats()
        return self.state()

    def state(self) -> JsonObject:
        """Повний стан для адміністративного запиту."""
        with self._lock:
            return {
                "mode": self._mode,
                "stats": self._stats.to_json(),
                "captures": len(self._captures),
                "config": {
                    "delaySeconds": self._config.delay_seconds,
                    "slowDelaySeconds": self._config.slow_delay_seconds,
                    "errorFraction": self._config.error_fraction,
                    "seed": self._config.seed,
                },
            }

    def handle_payment(
        self,
        body: JsonObject,
        idempotency_key: str | None,
    ) -> PaymentHttpResult:
        """Обробляє POST /payments."""
        with self._lock:
            self._stats.total += 1

        if idempotency_key is None or not idempotency_key.strip():
            return self._invalid("заголовок Idempotency-Key обов'язковий")
        if _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None:
            return self._invalid(
                "Idempotency-Key має формат order:<uuid>:payment"
            )

        try:
            order_id = as_str(field(body, "orderId"), "orderId")
            UUID(order_id)
            amount_text = as_str(field(body, "amount"), "amount")
            amount = Decimal(amount_text)
            if not amount.is_finite() or amount < 0:
                raise ValueError("amount має бути скінченним і невід'ємним")
            if amount != amount.quantize(Decimal("0.01")):
                raise ValueError("amount має містити не більше двох знаків")
            currency = as_str(field(body, "currency"), "currency")
            if currency not in ("UAH", "USD", "EUR"):
                raise ValueError(f"невідома валюта: {currency!r}")
        except (ValueError, InvalidOperation) as error:
            return self._invalid(str(error))

        fingerprint = f"{order_id}|{amount_text}|{currency}"
        key_lock = self._key_lock(idempotency_key)
        with key_lock:
            with self._lock:
                existing = self._captures.get(idempotency_key)
                mode = self._mode
                if existing is not None:
                    if existing.fingerprint != fingerprint:
                        self._stats.conflicts += 1
                        return PaymentHttpResult(
                            409,
                            _error(
                                "idempotency_conflict",
                                "ключ ідемпотентності вже використано з іншими даними",
                            ),
                        )
                    self._stats.replays += 1
                    return PaymentHttpResult(200, _with_replay(existing.response))

            if mode == "unavailable":
                with self._lock:
                    self._stats.unavailable += 1
                return PaymentHttpResult(0, None)

            if mode == "errors":
                with self._lock:
                    fail = self._random.random() < self._config.error_fraction
                    if fail:
                        self._stats.errors += 1
                if fail:
                    return PaymentHttpResult(
                        503,
                        _error(
                            "payment_unavailable",
                            "тимчасова відмова платіжного шлюзу",
                        ),
                    )

            if mode == "slow":
                time.sleep(self._config.slow_delay_seconds)
            else:
                time.sleep(self._config.delay_seconds)

            with self._lock:
                response: JsonObject = {
                    "paymentId": f"pay-{self._next_payment:06d}",
                    "orderId": order_id,
                    "status": "captured",
                    "amount": f"{amount:.2f}",
                    "currency": currency,
                    "capturedAt": datetime_to_json(datetime.now(UTC)),
                    "idempotentReplay": False,
                }
                self._next_payment += 1
                self._captures[idempotency_key] = _Capture(
                    fingerprint=fingerprint,
                    response=response,
                )
                self._stats.accepted += 1
                return PaymentHttpResult(201, dict(response))

    def _invalid(self, message: str) -> PaymentHttpResult:
        with self._lock:
            self._stats.invalid += 1
        return PaymentHttpResult(400, _error("invalid_request", message))

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock


def _with_replay(response: JsonObject) -> JsonObject:
    replay = dict(response)
    replay["idempotentReplay"] = True
    return replay


def _error(code: str, message: str) -> JsonObject:
    return {"error": {"code": code, "message": message}}


def make_server(
    config: PaymentServiceConfig,
) -> tuple[ThreadingHTTPServer, PaymentService]:
    """Створює HTTP-сервер; port=0 вибирає вільний порт."""
    service = PaymentService(config)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            path, _query = split_path(self.path)
            if path == "/health":
                send_json(
                    self,
                    200,
                    {"status": "ok", "mode": service.mode},
                )
                return
            if path == "/_admin/state":
                send_json(self, 200, service.state())
                return
            send_error(self, 404, "not_found", f"невідомий шлях {path}")

        def do_POST(self) -> None:
            path, _query = split_path(self.path)
            try:
                if path == "/payments":
                    body = read_json_body(self)
                    result = service.handle_payment(
                        body,
                        self.headers.get("Idempotency-Key"),
                    )
                    if result.body is None:
                        self.close_connection = True
                        return
                    send_json(self, result.status, result.body)
                    return
                if path == "/_admin/mode":
                    body = read_json_body(self)
                    mode = as_str(field(body, "mode"), "mode")
                    send_json(self, 200, service.set_mode(mode))
                    return
                if path == "/_admin/reset":
                    send_json(self, 200, service.reset_stats())
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
    config = PaymentServiceConfig.from_env()
    parser = argparse.ArgumentParser(description="Платіжний шлюз (ЛР3)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--delay", type=float, default=None)
    parser.add_argument("--slow-delay", type=float, default=None)
    parser.add_argument("--error-fraction", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    if args.host is not None:
        config = replace(config, host=args.host)
    if args.port is not None:
        config = replace(config, port=args.port)
    if args.delay is not None:
        config = replace(config, delay_seconds=args.delay)
    if args.slow_delay is not None:
        config = replace(config, slow_delay_seconds=args.slow_delay)
    if args.error_fraction is not None:
        config = replace(config, error_fraction=args.error_fraction)
    if args.seed is not None:
        config = replace(config, seed=args.seed)

    server, _service = make_server(config)
    print(
        f"payment service: http://{config.host}:{server.server_port} "
        f"(mode=normal)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
