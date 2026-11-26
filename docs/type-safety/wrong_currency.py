"""Навмисно некоректний тип валюти: рядок замість Currency.

Цей файл не входить у src/ і не запускається. Він потрібен лише для
демонстрації помилки mypy --strict у звіті.
"""

from decimal import Decimal

from shop.domain.money import Money

money = Money(Decimal("10.00"), "UAH")
