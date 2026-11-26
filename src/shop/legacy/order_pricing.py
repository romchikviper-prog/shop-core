"""Спадковий розрахунок ціни замовлення.

Модуль перенесено зі старої системи. Він працює, але написаний без
специфікації та тестів: одна довга функція, вкладені умови, магічні
числа й повторений код. Поведінку зафіксовано характеризаційними тестами.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

MONEY_STEP = Decimal("0.01")
ZERO = Decimal("0.00")
ROUNDING = ROUND_HALF_UP


@dataclass(frozen=True, slots=True)
class LegacyItem:
    name: str
    price: Decimal
    quantity: int


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_STEP, rounding=ROUNDING)


def _subtotal(items: list[LegacyItem]) -> tuple[Decimal, int]:
    subtotal = ZERO
    total_qty = 0
    for item in items:
        if item.quantity <= 0:
            continue
        subtotal = subtotal + item.price * item.quantity
        total_qty = total_qty + item.quantity
    return _money(subtotal), total_qty


def calculate_order_price(
    items: list[LegacyItem],
    customer_type: str,
    loyalty_years: int,
    promo_code: str | None,
    delivery: str,
) -> dict[str, object]:
    subtotal, total_qty = _subtotal(items)

    if total_qty >= 50:
        quantity_discount = subtotal * Decimal("0.15")
    elif total_qty >= 20:
        quantity_discount = subtotal * Decimal("0.10")
    elif total_qty >= 10:
        quantity_discount = subtotal * Decimal("0.05")
    else:
        quantity_discount = ZERO
    quantity_discount = _money(quantity_discount)

    coupon_discount = ZERO
    if promo_code is not None:
        if promo_code == "SAVE10":
            if subtotal >= Decimal("500.00"):
                coupon_discount = subtotal * Decimal("0.10")
        elif promo_code == "SAVE20":
            if subtotal >= Decimal("1000.00"):
                coupon_discount = subtotal * Decimal("0.20")
        elif promo_code == "MINUS100":
            if subtotal >= Decimal("800.00"):
                coupon_discount = Decimal("100.00")
    coupon_discount = _money(coupon_discount)

    loyalty_discount = ZERO
    if customer_type == "silver":
        loyalty_discount = subtotal * Decimal("0.03")
    elif customer_type == "gold":
        loyalty_discount = subtotal * Decimal("0.05")
    elif customer_type == "platinum":
        loyalty_discount = subtotal * Decimal("0.07")
    else:
        loyalty_discount = ZERO

    if loyalty_years >= 10:
        loyalty_discount = loyalty_discount + subtotal * Decimal("0.03")
    elif loyalty_years >= 5:
        loyalty_discount = loyalty_discount + subtotal * Decimal("0.02")

    loyalty_discount = _money(loyalty_discount)

    discounted = subtotal - quantity_discount - coupon_discount - loyalty_discount
    discounted = _money(discounted)

    tax = _money(discounted * Decimal("0.20"))

    if delivery == "pickup":
        delivery_cost = ZERO
    elif delivery == "courier":
        if discounted >= Decimal("1000.00"):
            delivery_cost = ZERO
        else:
            delivery_cost = Decimal("99.00")
    elif delivery == "express":
        if discounted >= Decimal("2000.00"):
            delivery_cost = ZERO
        else:
            delivery_cost = Decimal("199.00")
    else:
        delivery_cost = ZERO

    total = _money(discounted + tax + delivery_cost)

    result: dict[str, object] = {
        "subtotal": subtotal,
        "quantity_discount": quantity_discount,
        "coupon_discount": coupon_discount,
        "loyalty_discount": loyalty_discount,
        "tax": tax,
        "delivery": delivery_cost,
        "total": total,
        "promo_code": promo_code,
        "customer_type": customer_type,
    }
    return result
