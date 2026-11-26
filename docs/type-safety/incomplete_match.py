"""Навмисно неповний pattern matching: не оброблено Placed, Paid, Shipped, Cancelled.

Цей файл не входить у src/ і не запускається. Він потрібен лише для
демонстрації помилки mypy --strict у звіті.
"""

from typing import assert_never

from shop.domain.order import Draft, Order


def label(order: Order) -> str:
    match order:
        case Draft():
            return "draft"
        case _:
            assert_never(order)
