"""Події замовлень і детермінований генератор навантаження.

Варіант 2: 5 000 замовлень у розпродажу, 50 товарів, один «гарячий»
товар отримує понад половину замовлень. Генератор не використовує
випадковості, тому кожен запуск обробляє однаковий потік подій.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from shop.domain.ids import OrderId, Sku

HOT_SKU = Sku("SKU-0000")
PRODUCT_COUNT = 50
HOT_PERCENT = 60
HOT_STOCK = 100
OTHER_STOCK = 10_000


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """Замовлення як подія потоку: який товар і скільки одиниць резервувати."""

    seq: int
    order_id: OrderId
    sku: Sku
    quantity: int = 1

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise ValueError("seq не може бути від'ємним")
        if self.quantity < 1:
            raise ValueError("quantity має бути додатною")


def make_skus(products: int = PRODUCT_COUNT) -> tuple[Sku, ...]:
    """Повертає детермінований набір артикулів."""

    if products < 1:
        raise ValueError("products має бути додатним")
    if products > 9_999:
        raise ValueError("products не може перевищувати 9999")
    return tuple(Sku(f"SKU-{index:04d}") for index in range(products))


def generate_events(
    count: int,
    *,
    products: int = PRODUCT_COUNT,
    hot_percent: int = HOT_PERCENT,
) -> tuple[OrderEvent, ...]:
    """Генерує `count` замовлень; `hot_percent` відсотків припадає на SKU-0000."""

    if count < 1:
        raise ValueError("count має бути додатним")
    if not 0 <= hot_percent <= 100:
        raise ValueError("hot_percent має бути в межах 0..100")

    skus = make_skus(products)
    if products == 1 and hot_percent < 100:
        raise ValueError("для одного товару hot_percent має дорівнювати 100")

    events: list[OrderEvent] = []
    for seq in range(count):
        if seq % 100 < hot_percent:
            sku = skus[0]
        else:
            sku = skus[1 + (seq % (products - 1))]
        events.append(
            OrderEvent(
                seq=seq,
                order_id=OrderId(UUID(int=seq + 1)),
                sku=sku,
            )
        )
    return tuple(events)


def initial_stock(
    *,
    products: int = PRODUCT_COUNT,
    hot_quantity: int = HOT_STOCK,
    other_quantity: int = OTHER_STOCK,
) -> dict[Sku, int]:
    """Початковий залишок: гарячий товар обмежений, решта — великий запас."""

    if hot_quantity < 0 or other_quantity < 0:
        raise ValueError("залишки не можуть бути від'ємними")
    stock: dict[Sku, int] = {}
    for index, sku in enumerate(make_skus(products)):
        stock[sku] = hot_quantity if index == 0 else other_quantity
    return stock
