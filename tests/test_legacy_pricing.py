"""Характеризаційні тести спадкового розрахунку ціни (ЛР4, крок 1).

Очікувані значення знято з модуля до будь-яких змін і збережено у
``tests/lr4/pricing_expected.json``. Під час рефакторингу їх не можна
змінювати: саме вони доводять, що зовнішня поведінка не зламалася.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from shop.legacy.order_pricing import LegacyItem, calculate_order_price

DATA_DIR = Path(__file__).with_name("lr4")
CASES: list[dict[str, Any]] = json.loads(
    (DATA_DIR / "pricing_cases.json").read_text(encoding="utf-8")
)
EXPECTED: dict[str, dict[str, Any]] = json.loads(
    (DATA_DIR / "pricing_expected.json").read_text(encoding="utf-8")
)

MONEY_KEYS = (
    "subtotal",
    "quantity_discount",
    "coupon_discount",
    "loyalty_discount",
    "tax",
    "delivery",
    "total",
)


def _items(raw_items: list[dict[str, Any]]) -> list[LegacyItem]:
    return [
        LegacyItem(
            name=str(raw["name"]),
            price=Decimal(str(raw["price"])),
            quantity=int(raw["quantity"]),
        )
        for raw in raw_items
    ]


def _expected(raw: dict[str, Any]) -> dict[str, object]:
    result: dict[str, object] = {
        "promo_code": raw["promo_code"],
        "customer_type": raw["customer_type"],
    }
    for key in MONEY_KEYS:
        result[key] = Decimal(str(raw[key]))
    return result


@pytest.mark.parametrize("case", CASES, ids=[str(case["id"]) for case in CASES])
def test_legacy_pricing_characterization(case: dict[str, Any]) -> None:
    result = calculate_order_price(
        _items(case["items"]),
        str(case["customer_type"]),
        int(case["loyalty_years"]),
        case["promo_code"],
        str(case["delivery"]),
    )

    assert result == _expected(EXPECTED[str(case["id"])])
