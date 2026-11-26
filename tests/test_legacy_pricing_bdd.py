"""Кроки BDD-сценаріїв ЛР4 українською мовою.

Сценарії описують поведінку мовою предметної області: кошик, клієнт,
промокод, доставка, знижки, податок і підсумок. Назв класів і методів
у тексті сценаріїв немає.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from shop.legacy.order_pricing import LegacyItem, calculate_order_price

scenarios("features/order_pricing.feature")


@pytest.fixture
def context() -> dict[str, Any]:
    return {
        "customer_type": "regular",
        "loyalty_years": 0,
        "promo_code": None,
        "delivery": "pickup",
    }


@given("кошик:", target_fixture="cart")
def cart(datatable: list[list[str]]) -> list[LegacyItem]:
    items: list[LegacyItem] = []
    for name, price, quantity in datatable[1:]:
        items.append(
            LegacyItem(
                name=str(name),
                price=Decimal(str(price)),
                quantity=int(quantity),
            )
        )
    return items


@given(parsers.parse('клієнт має статус "{status}"'))
def customer_status(context: dict[str, Any], status: str) -> None:
    context["customer_type"] = status


@given(parsers.parse('введено промокод "{code}"'))
def promo_code(context: dict[str, Any], code: str) -> None:
    context["promo_code"] = code


@given(parsers.parse('обрано доставку "{delivery}"'))
def delivery(context: dict[str, Any], delivery: str) -> None:
    context["delivery"] = delivery


@when("я розраховую ціну замовлення", target_fixture="report")
def calculate(
    cart: list[LegacyItem], context: dict[str, Any]
) -> dict[str, object]:
    return calculate_order_price(
        cart,
        str(context["customer_type"]),
        int(context["loyalty_years"]),
        context["promo_code"],
        str(context["delivery"]),
    )


@then(parsers.parse("знижка за кількістю становить {amount} грн"))
def quantity_discount(report: dict[str, object], amount: str) -> None:
    assert report["quantity_discount"] == Decimal(amount)


@then(parsers.parse("знижка за промокодом становить {amount} грн"))
def coupon_discount(report: dict[str, object], amount: str) -> None:
    assert report["coupon_discount"] == Decimal(amount)


@then(parsers.parse("знижка за лояльністю становить {amount} грн"))
def loyalty_discount(report: dict[str, object], amount: str) -> None:
    assert report["loyalty_discount"] == Decimal(amount)


@then(parsers.parse("податок становить {amount} грн"))
def tax(report: dict[str, object], amount: str) -> None:
    assert report["tax"] == Decimal(amount)


@then(parsers.parse("вартість доставки становить {amount} грн"))
def delivery_cost(report: dict[str, object], amount: str) -> None:
    assert report["delivery"] == Decimal(amount)


@then(parsers.parse("підсумок становить {amount} грн"))
def total(report: dict[str, object], amount: str) -> None:
    assert report["total"] == Decimal(amount)


@then("у звіті немає промокоду")
def no_promo_code(report: dict[str, object]) -> None:
    assert report["promo_code"] is None
