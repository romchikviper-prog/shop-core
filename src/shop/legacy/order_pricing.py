"""Спадковий розрахунок ціни замовлення.

Модуль перенесено зі старої системи. Він працює, але написаний без
специфікації та тестів: одна довга функція, вкладені умови, магічні
числа й повторений код. Поведінку зафіксовано характеризаційними тестами.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

MONEY_STEP = Decimal("0.01")
ZERO = Decimal("0.00")
ROUNDING = ROUND_HALF_UP
TAX_RATE = Decimal("0.20")


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


@dataclass(frozen=True, slots=True)
class QuantityRule:
    min_quantity: int
    rate: Decimal


QUANTITY_RULES: tuple[QuantityRule, ...] = (
    QuantityRule(min_quantity=50, rate=Decimal("0.15")),
    QuantityRule(min_quantity=20, rate=Decimal("0.10")),
    QuantityRule(min_quantity=10, rate=Decimal("0.05")),
)


def _quantity_discount(subtotal: Decimal, total_qty: int) -> Decimal:
    for rule in QUANTITY_RULES:
        if total_qty >= rule.min_quantity:
            return _money(subtotal * rule.rate)
    return ZERO


class CouponCode(StrEnum):
    SAVE10 = "SAVE10"
    SAVE20 = "SAVE20"
    MINUS100 = "MINUS100"


@dataclass(frozen=True, slots=True)
class CouponRule:
    min_subtotal: Decimal
    percent: Decimal
    fixed: Decimal


COUPON_RULES: dict[CouponCode, CouponRule] = {
    CouponCode.SAVE10: CouponRule(
        min_subtotal=Decimal("500.00"), percent=Decimal("0.10"), fixed=ZERO
    ),
    CouponCode.SAVE20: CouponRule(
        min_subtotal=Decimal("1000.00"), percent=Decimal("0.20"), fixed=ZERO
    ),
    CouponCode.MINUS100: CouponRule(
        min_subtotal=Decimal("800.00"), percent=ZERO, fixed=Decimal("100.00")
    ),
}


def _coupon_discount(subtotal: Decimal, promo_code: str | None) -> Decimal:
    if promo_code is None:
        return ZERO
    try:
        code = CouponCode(promo_code)
    except ValueError:
        return ZERO
    rule = COUPON_RULES[code]
    if subtotal < rule.min_subtotal:
        return ZERO
    return _money(subtotal * rule.percent + rule.fixed)


class CustomerStatus(StrEnum):
    REGULAR = "regular"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"


LOYALTY_RATES: dict[CustomerStatus, Decimal] = {
    CustomerStatus.REGULAR: ZERO,
    CustomerStatus.SILVER: Decimal("0.03"),
    CustomerStatus.GOLD: Decimal("0.05"),
    CustomerStatus.PLATINUM: Decimal("0.07"),
}


@dataclass(frozen=True, slots=True)
class LoyaltyYearsRule:
    min_years: int
    bonus: Decimal


LOYALTY_YEARS_RULES: tuple[LoyaltyYearsRule, ...] = (
    LoyaltyYearsRule(min_years=10, bonus=Decimal("0.03")),
    LoyaltyYearsRule(min_years=5, bonus=Decimal("0.02")),
)


def _parse_customer_status(customer_type: str) -> CustomerStatus:
    try:
        return CustomerStatus(customer_type)
    except ValueError:
        return CustomerStatus.REGULAR


def _loyalty_discount(
    subtotal: Decimal, customer_type: str, loyalty_years: int
) -> Decimal:
    status = _parse_customer_status(customer_type)
    rate = LOYALTY_RATES[status]
    for rule in LOYALTY_YEARS_RULES:
        if loyalty_years >= rule.min_years:
            rate = rate + rule.bonus
            break
    return _money(subtotal * rate)


class DeliveryMethod(StrEnum):
    PICKUP = "pickup"
    COURIER = "courier"
    EXPRESS = "express"


@dataclass(frozen=True, slots=True)
class DeliveryRule:
    price: Decimal
    free_from: Decimal | None


DELIVERY_RULES: dict[DeliveryMethod, DeliveryRule] = {
    DeliveryMethod.PICKUP: DeliveryRule(price=ZERO, free_from=None),
    DeliveryMethod.COURIER: DeliveryRule(
        price=Decimal("99.00"), free_from=Decimal("1000.00")
    ),
    DeliveryMethod.EXPRESS: DeliveryRule(
        price=Decimal("199.00"), free_from=Decimal("2000.00")
    ),
}


def _parse_delivery(delivery: str) -> DeliveryMethod:
    try:
        return DeliveryMethod(delivery)
    except ValueError:
        return DeliveryMethod.PICKUP


def _delivery_cost(delivery: str, discounted: Decimal) -> Decimal:
    rule = DELIVERY_RULES[_parse_delivery(delivery)]
    if rule.free_from is not None and discounted >= rule.free_from:
        return ZERO
    return rule.price


def _tax(discounted: Decimal) -> Decimal:
    return _money(discounted * TAX_RATE)


def _final_total(
    discounted: Decimal, tax: Decimal, delivery_cost: Decimal
) -> Decimal:
    return _money(discounted + tax + delivery_cost)


@dataclass(frozen=True, slots=True)
class DiscountSelection:
    quantity: Decimal
    coupon: Decimal
    loyalty: Decimal


def _apply_largest_discount(
    quantity_discount: Decimal,
    coupon_discount: Decimal,
    loyalty_discount: Decimal,
) -> DiscountSelection:
    candidates = (quantity_discount, coupon_discount, loyalty_discount)
    largest = max(candidates)
    selected = [ZERO, ZERO, ZERO]
    selected[candidates.index(largest)] = largest
    return DiscountSelection(
        quantity=selected[0], coupon=selected[1], loyalty=selected[2]
    )


@dataclass(frozen=True, slots=True)
class PricingRequest:
    items: list[LegacyItem]
    customer_type: str
    loyalty_years: int
    promo_code: str | None
    delivery: str


@dataclass(frozen=True, slots=True)
class PricingBreakdown:
    subtotal: Decimal
    quantity_discount: Decimal
    coupon_discount: Decimal
    loyalty_discount: Decimal
    tax: Decimal
    delivery_cost: Decimal
    total: Decimal
    promo_code: str | None
    customer_type: str


def _discounted(
    subtotal: Decimal,
    quantity_discount: Decimal,
    coupon_discount: Decimal,
    loyalty_discount: Decimal,
) -> Decimal:
    return _money(
        subtotal - quantity_discount - coupon_discount - loyalty_discount
    )


def _build_report(breakdown: PricingBreakdown) -> dict[str, object]:
    return {
        "subtotal": breakdown.subtotal,
        "quantity_discount": breakdown.quantity_discount,
        "coupon_discount": breakdown.coupon_discount,
        "loyalty_discount": breakdown.loyalty_discount,
        "tax": breakdown.tax,
        "delivery": breakdown.delivery_cost,
        "total": breakdown.total,
        "promo_code": breakdown.promo_code,
        "customer_type": breakdown.customer_type,
    }


def calculate_price(request: PricingRequest) -> dict[str, object]:
    subtotal, total_qty = _subtotal(request.items)
    discounts = _apply_largest_discount(
        _quantity_discount(subtotal, total_qty),
        _coupon_discount(subtotal, request.promo_code),
        _loyalty_discount(subtotal, request.customer_type, request.loyalty_years),
    )
    discounted = _discounted(
        subtotal, discounts.quantity, discounts.coupon, discounts.loyalty
    )
    tax = _tax(discounted)
    delivery_cost = _delivery_cost(request.delivery, discounted)
    total = _final_total(discounted, tax, delivery_cost)

    return _build_report(
        PricingBreakdown(
            subtotal=subtotal,
            quantity_discount=discounts.quantity,
            coupon_discount=discounts.coupon,
            loyalty_discount=discounts.loyalty,
            tax=tax,
            delivery_cost=delivery_cost,
            total=total,
            promo_code=request.promo_code,
            customer_type=request.customer_type,
        )
    )


def calculate_order_price(
    items: list[LegacyItem],
    customer_type: str,
    loyalty_years: int,
    promo_code: str | None,
    delivery: str,
) -> dict[str, object]:
    return calculate_price(
        PricingRequest(
            items=items,
            customer_type=customer_type,
            loyalty_years=loyalty_years,
            promo_code=promo_code,
            delivery=delivery,
        )
    )
