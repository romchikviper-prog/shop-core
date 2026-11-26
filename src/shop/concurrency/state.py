"""Спільний стан складу: небезпечна версія та два виправлення.

Інваріант варіанта 2: залишок не стає від'ємним, а одне замовлення
резервує товар не більше ніж один раз. Усі реалізації мають однаковий
інтерфейс, тому конвеєри й тести не залежать від способу синхронізації.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import dataclass, replace
from typing import Protocol

from shop.domain.ids import OrderId, Sku
from shop.domain.inventory import StockItem
from shop.domain.result import Ok

from .model import OrderEvent


class StockStore(Protocol):
    """Спільний стан складу, який уміє резервувати одиницю товару."""

    def reserve(self, event: OrderEvent) -> bool: ...

    def available(self, sku: Sku) -> int: ...

    def reserved(self, sku: Sku) -> int: ...

    def accepted_order_ids(self) -> tuple[OrderId, ...]: ...

    def rejected_order_ids(self) -> tuple[OrderId, ...]: ...


def _reserve_item(item: StockItem, quantity: int) -> StockItem | None:
    """Повертає новий StockItem або None, якщо резервування неможливе."""

    result = item.reserve(quantity)
    if isinstance(result, Ok):
        return result.value
    return None


class _StockBase:
    """Спільні поля й читання стану без припущень про синхронізацію."""

    def __init__(self, stock: Mapping[Sku, int]) -> None:
        self._stock: dict[Sku, StockItem] = {
            sku: StockItem(sku=sku, available=quantity)
            for sku, quantity in stock.items()
        }
        self._decisions: dict[OrderId, bool] = {}
        self._accepted: list[OrderId] = []
        self._rejected: list[OrderId] = []

    def available(self, sku: Sku) -> int:
        item = self._stock.get(sku)
        return item.available if item is not None else 0

    def reserved(self, sku: Sku) -> int:
        item = self._stock.get(sku)
        return item.reserved if item is not None else 0

    def accepted_order_ids(self) -> tuple[OrderId, ...]:
        return tuple(self._accepted)

    def rejected_order_ids(self) -> tuple[OrderId, ...]:
        return tuple(self._rejected)

    def _record(self, event: OrderEvent, accepted: bool) -> None:
        self._decisions[event.order_id] = accepted
        if accepted:
            self._accepted.append(event.order_id)
        else:
            self._rejected.append(event.order_id)

    def _reserve_locked(self, event: OrderEvent) -> bool:
        """Атомарна перевірка й зміна; викликається лише під захистом."""

        previous = self._decisions.get(event.order_id)
        if previous is not None:
            return previous

        item = self._stock.get(event.sku)
        if item is None or item.available < event.quantity:
            self._record(event, False)
            return False

        updated = _reserve_item(item, event.quantity)
        if updated is None:
            self._record(event, False)
            return False

        self._stock[event.sku] = updated
        self._record(event, True)
        return True


class UnsafeStockStore(_StockBase):
    """Навмисно небезпечна версія: get → перевірка → put без блокування.

    `time.sleep(0)` стоїть у вікні між читанням і записом, щоб дефект
    відтворювався стабільно, а не залежав від моменту перемикання GIL.
    Це не очікування результату, а модель реального вікна конкурентності.
    """

    def reserve(self, event: OrderEvent) -> bool:
        previous = self._decisions.get(event.order_id)
        if previous is not None:
            return previous

        item = self._stock.get(event.sku)
        if item is None or item.available < event.quantity:
            self._record(event, False)
            return False

        # Вікно між читанням і записом: інший потік може прочитати те саме.
        time.sleep(0)

        updated = replace(
            item,
            available=item.available - event.quantity,
            reserved=item.reserved + event.quantity,
        )
        self._stock[event.sku] = updated
        self._record(event, True)
        return True


class LockedStockStore(_StockBase):
    """Виправлення блокуванням: увесь інваріант перевіряється під Lock."""

    def __init__(self, stock: Mapping[Sku, int]) -> None:
        super().__init__(stock)
        self._lock = threading.Lock()

    def reserve(self, event: OrderEvent) -> bool:
        with self._lock:
            return self._reserve_locked(event)

    def available(self, sku: Sku) -> int:
        with self._lock:
            return super().available(sku)

    def reserved(self, sku: Sku) -> int:
        with self._lock:
            return super().reserved(sku)

    def accepted_order_ids(self) -> tuple[OrderId, ...]:
        with self._lock:
            return super().accepted_order_ids()

    def rejected_order_ids(self) -> tuple[OrderId, ...]:
        with self._lock:
            return super().rejected_order_ids()


@dataclass(frozen=True, slots=True)
class _ReserveCommand:
    event: OrderEvent
    future: Future[bool]


class OwnerStockStore(_StockBase):
    """Виправлення ізоляцією: стан змінює лише один потік-власник.

    Виклики з інших потоків передають команду через `queue.Queue` і чекають
    на Future. Це найдорожчий варіант за часом, але всередині власника
    немає блокувань і порядок команд повністю визначений.
    """

    def __init__(self, stock: Mapping[Sku, int]) -> None:
        super().__init__(stock)
        self._commands: queue.Queue[_ReserveCommand | None] = queue.Queue()
        self._closed = False
        self._owner = threading.Thread(
            target=self._run,
            name="stock-owner",
            daemon=True,
        )
        self._owner.start()

    def reserve(self, event: OrderEvent) -> bool:
        if self._closed:
            raise RuntimeError("OwnerStockStore уже закрито")
        future: Future[bool] = Future()
        self._commands.put(_ReserveCommand(event=event, future=future))
        return future.result()

    def _run(self) -> None:
        while True:
            command = self._commands.get()
            if command is None:
                return
            if not command.future.set_running_or_notify_cancel():
                continue
            try:
                command.future.set_result(self._reserve_locked(command.event))
            except BaseException as error:  # noqa: BLE001 - переносимо у Future
                command.future.set_exception(error)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._commands.put(None)
        self._owner.join()

    def __enter__(self) -> OwnerStockStore:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
