"""Навмисно некоректний приклад: перехід, якого немає в стані Draft.

Цей файл не входить у src/ і не запускається. Він потрібен лише для
демонстрації помилки mypy --strict у звіті.
"""

from datetime import UTC, datetime

from shop.domain.ids import CustomerId, OrderId
from shop.domain.order import Draft

draft = Draft(OrderId.new(), CustomerId.new(), (), datetime.now(UTC))
draft.ship("NP-123456789")
