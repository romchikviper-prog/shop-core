"""Спадковий розрахунок ціни замовлення.

Модуль перенесено зі старої системи. Він працює, але написаний без
специфікації та тестів: одна довга функція, вкладені умови, магічні
числа й повторений код. Поведінку зафіксовано характеризаційними тестами.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True, slots=True)
class LegacyItem:
    name: str
    price: Decimal
    quantity: int


def calculate_order_price(
    items: list[LegacyItem],
    customer_type: str,
    loyalty_years: int,
    promo_code: str | None,
    delivery: str,
) -> dict[str, object]:
    subtotal = Decimal("0.00")
    total_qty = 0
    for item in items:
        if item.quantity <= 0:
            continue
        subtotal = subtotal + item.price * item.quantity
        total_qty = total_qty + item.quantity

    subtotal = subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if total_qty >= 50:
        quantity_discount = subtotal * Decimal("0.15")
    elif total_qty >= 20:
        quantity_discount = subtotal * Decimal("0.10")
    elif total_qty >= 10:
        quantity_discount = subtotal * Decimal("0.05")
    else:
        quantity_discount = Decimal("0.00")
    quantity_discount = quantity_discount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    coupon_discount = Decimal("0.00")
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
    coupon_discount = coupon_discount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    loyalty_discount = Decimal("0.00")
    if customer_type == "silver":
        loyalty_discount = subtotal * Decimal("0.03")
    elif customer_type == "gold":
        loyalty_discount = subtotal * Decimal("0.05")
    elif customer_type == "platinum":
        loyalty_discount = subtotal * Decimal("0.07")
    else:
        loyalty_discount = Decimal("0.00")

    if loyalty_years >= 10:
        loyalty_discount = loyalty_discount + subtotal * Decimal("0.03")
    elif loyalty_years >= 5:
        loyalty_discount = loyalty_discount + subtotal * Decimal("0.02")

    loyalty_discount = loyalty_discount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    discounted = subtotal - quantity_discount - coupon_discount - loyalty_discount
    discounted = discounted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    tax = (discounted * Decimal("0.20")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if delivery == "pickup":
        delivery_cost = Decimal("0.00")
    elif delivery == "courier":
        if discounted >= Decimal("1000.00"):
            delivery_cost = Decimal("0.00")
        else:
            delivery_cost = Decimal("99.00")
    elif delivery == "express":
        if discounted >= Decimal("2000.00"):
            delivery_cost = Decimal("0.00")
        else:
            delivery_cost = Decimal("199.00")
    else:
        delivery_cost = Decimal("0.00")

    total = (discounted + tax + delivery_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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
