"""Проєкція для запитів, checkpoint і повторне програвання журналу.

Проєкція — денормалізований список замовлень за статусом. Її оновлює
окремий потік, тому модель читання відстає від журналу. Checkpoint
зберігається разом зі знімком проєкції, що дозволяє продовжити після
перезапуску без повторного читання всього журналу.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .event_store import EventStore, StoredEvent
from .jsonutil import (
    JsonObject,
    as_int,
    as_list,
    as_object,
    as_str,
    datetime_to_json,
    field,
)
from .model import OrderPlaced, OrderCancelled, PaymentCaptured, event_from_json


@dataclass(frozen=True, slots=True)
class OrderView:
    """Рядок проєкції для запиту списку замовлень."""

    order_id: str
    customer_id: str
    status: str
    total: str
    currency: str
    line_count: int
    version: int
    updated_at: str

    def to_json(self) -> JsonObject:
        """Серіалізує рядок проєкції."""
        return {
            "orderId": self.order_id,
            "customerId": self.customer_id,
            "status": self.status,
            "total": self.total,
            "currency": self.currency,
            "lineCount": self.line_count,
            "version": self.version,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class ProjectionSnapshot:
    """Знімок проєкції та позиція останньої застосованої події."""

    checkpoint: int
    orders: tuple[OrderView, ...]

    def to_json(self) -> JsonObject:
        """Серіалізує знімок."""
        return {
            "checkpoint": self.checkpoint,
            "orders": [order.to_json() for order in self.orders],
        }

    @classmethod
    def from_json(cls, value: object) -> ProjectionSnapshot:
        """Розбирає знімок; пошкоджений файл вважається відсутнім."""
        payload = as_object(value, "projection snapshot")
        checkpoint = as_int(field(payload, "checkpoint"), "checkpoint")
        orders = tuple(
            _order_view_from_json(item)
            for item in as_list(field(payload, "orders"), "orders")
        )
        return cls(checkpoint=checkpoint, orders=orders)


def _order_view_from_json(value: object) -> OrderView:
    payload = as_object(value, "order view")
    return OrderView(
        order_id=as_str(field(payload, "orderId"), "orderId"),
        customer_id=as_str(field(payload, "customerId"), "customerId"),
        status=as_str(field(payload, "status"), "status"),
        total=as_str(field(payload, "total"), "total"),
        currency=as_str(field(payload, "currency"), "currency"),
        line_count=as_int(field(payload, "lineCount"), "lineCount"),
        version=as_int(field(payload, "version"), "version"),
        updated_at=as_str(field(payload, "updatedAt"), "updatedAt"),
    )


class Projection:
    """Поточний стан проєкції; усі операції потокобезпечні."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._orders: dict[str, OrderView] = {}
        self._checkpoint = 0

    @property
    def checkpoint(self) -> int:
        """Позиція останньої застосованої події."""
        with self._lock:
            return self._checkpoint

    def apply(self, stored: StoredEvent) -> None:
        """Застосовує одну подію журналу."""
        with self._lock:
            if stored.global_position != self._checkpoint + 1:
                raise ValueError(
                    "проєкція очікує позицію "
                    f"{self._checkpoint + 1}, отримала {stored.global_position}"
                )
            event = event_from_json(stored.event_type, stored.payload)
            match event:
                case OrderPlaced(
                    order_id=order_id,
                    customer_id=customer_id,
                    lines=lines,
                    total=total,
                    at=at,
                ):
                    view = OrderView(
                        order_id=str(order_id),
                        customer_id=str(customer_id),
                        status="placed",
                        total=f"{total.amount:.2f}",
                        currency=total.currency.value,
                        line_count=len(lines),
                        version=stored.version,
                        updated_at=datetime_to_json(at),
                    )
                case PaymentCaptured(order_id=order_id, at=at):
                    existing = self._orders.get(str(order_id))
                    if existing is None:
                        raise ValueError(f"платіж для невідомого замовлення {order_id}")
                    view = replace(
                        existing,
                        status="paid",
                        version=stored.version,
                        updated_at=datetime_to_json(at),
                    )
                case OrderCancelled(order_id=order_id, at=at):
                    existing = self._orders.get(str(order_id))
                    if existing is None:
                        raise ValueError(f"скасування невідомого замовлення {order_id}")
                    view = replace(
                        existing,
                        status="cancelled",
                        version=stored.version,
                        updated_at=datetime_to_json(at),
                    )
            self._orders[view.order_id] = view
            self._checkpoint = stored.global_position

    def rebuild(self, events: tuple[StoredEvent, ...]) -> None:
        """Повністю перебудовує проєкцію з журналу."""
        with self._lock:
            self._orders.clear()
            self._checkpoint = 0
            for stored in events:
                self.apply(stored)

    def load(self, snapshot: ProjectionSnapshot) -> None:
        """Відновлює проєкцію зі збереженого знімка."""
        with self._lock:
            self._orders = {order.order_id: order for order in snapshot.orders}
            self._checkpoint = snapshot.checkpoint

    def snapshot(self) -> ProjectionSnapshot:
        """Незмінний знімок для збереження."""
        with self._lock:
            orders = tuple(
                self._orders[key] for key in sorted(self._orders)
            )
            return ProjectionSnapshot(checkpoint=self._checkpoint, orders=orders)

    def list_orders(self, status: str | None) -> tuple[OrderView, ...]:
        """Список замовлень, за потреби відфільтрований за статусом."""
        with self._lock:
            orders = [
                view
                for view in self._orders.values()
                if status is None or view.status == status
            ]
            return tuple(sorted(orders, key=lambda view: view.order_id))

    def get_order(self, order_id: str) -> OrderView | None:
        """Одне замовлення проєкції."""
        with self._lock:
            return self._orders.get(order_id)

    def checksum(self) -> str:
        """Контрольна сума для порівняння двох проєкцій."""
        snapshot = self.snapshot()
        raw = json.dumps(
            [order.to_json() for order in snapshot.orders],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class ProjectionStore:
    """Зберігає знімок проєкції у JSON-файлі."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> ProjectionSnapshot | None:
        """Читає знімок; відсутній або пошкоджений файл — None."""
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return ProjectionSnapshot.from_json(raw)
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def save(self, snapshot: ProjectionSnapshot) -> None:
        """Атомарно записує знімок."""
        temporary = self._path.with_name(self._path.name + ".tmp")
        temporary.write_text(
            json.dumps(snapshot.to_json(), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self._path)


@dataclass(frozen=True, slots=True)
class ProjectionLag:
    """Відставання проєкції від журналу."""

    checkpoint: int
    head: int
    lag_events: int
    lag_ms: float
    last_applied_at: str | None

    def to_json(self) -> JsonObject:
        """Серіалізує вимірювання відставання."""
        return {
            "checkpoint": self.checkpoint,
            "head": self.head,
            "lagEvents": self.lag_events,
            "lagMs": round(self.lag_ms, 3),
            "lastAppliedAt": self.last_applied_at,
        }


class Projector:
    """Фоновий потік, що переносить події з журналу в проєкцію."""

    def __init__(
        self,
        store: EventStore,
        projection: Projection,
        snapshot_store: ProjectionStore,
        poll_interval: float = 0.01,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval має бути додатним")
        self._store = store
        self._projection = projection
        self._snapshot_store = snapshot_store
        self._poll_interval = poll_interval
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_applied_at: datetime | None = None

    def start(self) -> None:
        """Запускає фоновий потік."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="order-projection",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Зупиняє фоновий потік."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.catch_up()
            except Exception as exc:  # журнал може бути пошкоджений
                print(f"projector: {exc}", file=sys.stderr, flush=True)
            self._stop.wait(self._poll_interval)

    def catch_up(self) -> None:
        """Застосовує всі нові події та зберігає знімок."""
        with self._lock:
            events = self._store.read_from(self._projection.checkpoint)
            if not events:
                return
            for stored in events:
                self._projection.apply(stored)
                self._last_applied_at = stored.recorded_at
            self._snapshot_store.save(self._projection.snapshot())

    def rebuild(self) -> None:
        """Переграє журнал із початку та замінює проєкцію."""
        with self._lock:
            events = self._store.read_all()
            self._projection.rebuild(events)
            self._last_applied_at = events[-1].recorded_at if events else None
            self._snapshot_store.save(self._projection.snapshot())

    def orders(self, status: str | None) -> tuple[OrderView, ...]:
        """Читає список із проєкції."""
        return self._projection.list_orders(status)

    def order(self, order_id: str) -> OrderView | None:
        """Читає одне замовлення з проєкції."""
        return self._projection.get_order(order_id)

    def checksum(self) -> str:
        """Контрольна сума поточної проєкції."""
        return self._projection.checksum()

    def lag(self) -> ProjectionLag:
        """Поточне відставання проєкції."""
        with self._lock:
            head = self._store.head()
            checkpoint = self._projection.checkpoint
            lag_events = max(0, head - checkpoint)
            if lag_events == 0 or self._last_applied_at is None:
                lag_ms = 0.0
            else:
                lag_ms = max(
                    0.0,
                    (datetime.now(UTC) - self._last_applied_at).total_seconds() * 1000.0,
                )
            return ProjectionLag(
                checkpoint=checkpoint,
                head=head,
                lag_events=lag_events,
                lag_ms=lag_ms,
                last_applied_at=(
                    None
                    if self._last_applied_at is None
                    else datetime_to_json(self._last_applied_at)
                ),
            )
