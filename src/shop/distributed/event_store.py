"""Журнал подій JSON Lines з оптимістичним блокуванням.

Один рядок — одна подія. Потік (stream) — це одне замовлення; версія
потоку зростає на 1 з кожною подією. Додавання вимагає очікуваної
версії: якщо інший запис уже змінив потік, операція відхиляється
конфліктом. Ідентифікатор команди зберігається в журналі, тому повтор
тієї самої команди не створює другу подію.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .jsonutil import (
    JsonObject,
    as_int,
    as_object,
    as_str,
    datetime_from_json,
    datetime_to_json,
    field,
)
from .model import (
    OrderEvent,
    OrderState,
    event_from_json,
    event_to_json,
    event_type_name,
    fold_events,
    order_id_from_text,
)


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """Подія разом із позицією в журналі та версією потоку."""

    global_position: int
    stream_id: str
    version: int
    event_type: str
    command_id: str
    payload: JsonObject
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class AppendResult:
    """Результат додавання: нові події або повтор уже відомої команди."""

    stored: tuple[StoredEvent, ...]
    duplicate: bool


class VersionConflict(Exception):
    """Очікувана версія потоку не збіглася з фактичною."""

    def __init__(self, stream_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"конфлікт версій потоку {stream_id}: очікувалась {expected}, фактична {actual}"
        )
        self.stream_id = stream_id
        self.expected = expected
        self.actual = actual


class EventStore:
    """Додає події у файл і відновлює стан потоків."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._events: list[StoredEvent] = []
        self._stream_versions: dict[str, int] = {}
        self._commands: dict[str, tuple[StoredEvent, ...]] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    @property
    def path(self) -> Path:
        """Шлях до файлу журналу."""
        return self._path

    def _load(self) -> None:
        if not self._path.exists():
            return
        commands: dict[str, list[StoredEvent]] = {}
        with self._path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    raw = json.loads(text)
                    stored = _stored_from_json(raw)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(
                        f"{self._path}:{line_number}: некоректний запис журналу: {exc}"
                    ) from exc
                self._events.append(stored)
                current = self._stream_versions.get(stored.stream_id, 0)
                self._stream_versions[stored.stream_id] = max(current, stored.version)
                commands.setdefault(stored.command_id, []).append(stored)
        self._commands = {key: tuple(value) for key, value in commands.items()}

    def append(
        self,
        *,
        command_id: str,
        stream_id: str,
        expected_version: int,
        events: Sequence[OrderEvent],
    ) -> AppendResult:
        """Додає події, якщо версія потоку не змінилася."""
        if not events:
            raise ValueError("не можна додати порожній список подій")
        if expected_version < 0:
            raise ValueError("expected_version не може бути від'ємною")
        with self._lock:
            existing = self._commands.get(command_id)
            if existing is not None:
                return AppendResult(existing, duplicate=True)

            actual = self._stream_versions.get(stream_id, 0)
            if actual != expected_version:
                raise VersionConflict(stream_id, expected_version, actual)

            recorded_at = datetime.now(UTC)
            stored_events: list[StoredEvent] = []
            next_position = len(self._events) + 1
            for offset, event in enumerate(events, start=1):
                stored_events.append(
                    StoredEvent(
                        global_position=next_position + offset - 1,
                        stream_id=stream_id,
                        version=expected_version + offset,
                        event_type=event_type_name(event),
                        command_id=command_id,
                        payload=event_to_json(event),
                        recorded_at=recorded_at,
                    )
                )

            with self._path.open("a", encoding="utf-8") as handle:
                for stored in stored_events:
                    handle.write(
                        json.dumps(
                            _stored_to_json(stored),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                handle.flush()
                os.fsync(handle.fileno())

            self._events.extend(stored_events)
            self._stream_versions[stream_id] = expected_version + len(stored_events)
            result = tuple(stored_events)
            self._commands[command_id] = result
            return AppendResult(result, duplicate=False)

    def find_command(self, command_id: str) -> tuple[StoredEvent, ...] | None:
        """Повертає події вже виконаної команди."""
        with self._lock:
            return self._commands.get(command_id)

    def read_all(self) -> tuple[StoredEvent, ...]:
        """Усі події журналу в порядку додавання."""
        with self._lock:
            return tuple(self._events)

    def read_from(self, position: int) -> tuple[StoredEvent, ...]:
        """Події з позицією, більшою за задану."""
        with self._lock:
            return tuple(
                event for event in self._events if event.global_position > position
            )

    def read_stream(self, stream_id: str) -> tuple[StoredEvent, ...]:
        """Усі події одного потоку."""
        with self._lock:
            return tuple(
                event for event in self._events if event.stream_id == stream_id
            )

    def stream_version(self, stream_id: str) -> int:
        """Поточна версія потоку."""
        with self._lock:
            return self._stream_versions.get(stream_id, 0)

    def head(self) -> int:
        """Позиція останньої події; 0 для порожнього журналу."""
        with self._lock:
            return len(self._events)

    def rebuild_state(self, stream_id: str) -> OrderState:
        """Відновлює агрегат із подій потоку."""
        events = tuple(
            event_from_json(stored.event_type, stored.payload)
            for stored in self.read_stream(stream_id)
        )
        return fold_events(order_id_from_text(stream_id), events)


def _stored_to_json(stored: StoredEvent) -> JsonObject:
    return {
        "globalPosition": stored.global_position,
        "streamId": stored.stream_id,
        "version": stored.version,
        "eventType": stored.event_type,
        "commandId": stored.command_id,
        "payload": stored.payload,
        "recordedAt": datetime_to_json(stored.recorded_at),
    }


def _stored_from_json(value: object) -> StoredEvent:
    payload = as_object(value, "stored event")
    return StoredEvent(
        global_position=as_int(field(payload, "globalPosition"), "globalPosition"),
        stream_id=as_str(field(payload, "streamId"), "streamId"),
        version=as_int(field(payload, "version"), "version"),
        event_type=as_str(field(payload, "eventType"), "eventType"),
        command_id=as_str(field(payload, "commandId"), "commandId"),
        payload=as_object(field(payload, "payload"), "payload"),
        recorded_at=datetime_from_json(field(payload, "recordedAt"), "recordedAt"),
    )
