"""TDD-тести нової вимоги ЛР4 (варіант 2).

Вимога: знижки не складаються, застосовується лише найбільша;
промокод без впливу не зберігається у звіті.
"""

from __future__ import annotations

from decimal import Decimal

from shop.legacy.order_pricing import LegacyItem, calculate_order_price


def test_only_largest_discount_is_applied() -> None:
    result = calculate_order_price(
        [LegacyItem("A", Decimal("100.00"), 10)],
        "gold",
        0,
        "SAVE20",
        "pickup",
    )

    assert result["quantity_discount"] == Decimal("0.00")
    assert result["coupon_discount"] == Decimal("200.00")
    assert result["loyalty_discount"] == Decimal("0.00")
    assert result["total"] == Decimal("960.00")
    assert result["promo_code"] == "SAVE20"
