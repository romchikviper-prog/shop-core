"""Тести прикладної логіки HTTP-сервісу."""

from __future__ import annotations

from typing import Any

import pytest

from shop.api.service import RequestError, evaluate


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "items": [{"name": "Телефон", "price": "100.00", "quantity": 10}],
        "customer_type": "gold",
        "loyalty_years": 0,
        "promo_code": "SAVE20",
        "delivery": "pickup",
    }
    payload.update(overrides)
    return payload


def test_defaults_produce_regular_pickup_order() -> None:
    result = evaluate({"items": [{"name": "Телефон", "price": "100.00", "quantity": 1}]})

    assert result["subtotal"] == "100.00"
    assert result["tax"] == "20.00"
    assert result["delivery"] == "0.00"
    assert result["total"] == "120.00"
    assert result["promo_code"] is None
    assert result["customer_type"] == "regular"


def test_applies_only_largest_discount() -> None:
    result = evaluate(_payload())

    assert result["quantity_discount"] == "0.00"
    assert result["coupon_discount"] == "200.00"
    assert result["loyalty_discount"] == "0.00"
    assert result["tax"] == "160.00"
    assert result["total"] == "960.00"
    assert result["promo_code"] == "SAVE20"


def test_quantity_discount_when_it_is_largest() -> None:
    result = evaluate(_payload(customer_type="regular", promo_code=None))

    assert result["quantity_discount"] == "50.00"
    assert result["tax"] == "190.00"
    assert result["total"] == "1140.00"


def test_loyalty_discount_includes_years_bonus() -> None:
    result = evaluate(
        _payload(
            customer_type="platinum",
            loyalty_years=10,
            promo_code=None,
        )
    )

    assert result["loyalty_discount"] == "100.00"
    assert result["total"] == "1080.00"


def test_courier_delivery_is_free_after_threshold() -> None:
    result = evaluate(
        {
            "items": [{"name": "Ноутбук", "price": "1500.00", "quantity": 1}],
            "delivery": "courier",
        }
    )

    assert result["delivery"] == "0.00"
    assert result["total"] == "1800.00"


def test_courier_delivery_is_charged_below_threshold() -> None:
    result = evaluate(
        {
            "items": [{"name": "Мишка", "price": "500.00", "quantity": 1}],
            "delivery": "courier",
        }
    )

    assert result["delivery"] == "99.00"
    assert result["total"] == "699.00"


def test_unknown_values_fall_back_to_legacy_defaults() -> None:
    result = evaluate(
        {
            "items": [{"name": "Телефон", "price": "100.00", "quantity": 1}],
            "customer_type": "vip",
            "delivery": "drone",
        }
    )

    assert result["customer_type"] == "vip"
    assert result["delivery"] == "0.00"
    assert result["total"] == "120.00"


def test_zero_quantity_is_ignored() -> None:
    result = evaluate(
        {
            "items": [
                {"name": "Телефон", "price": "100.00", "quantity": 0},
                {"name": "Мишка", "price": "50.00", "quantity": 1},
            ]
        }
    )

    assert result["subtotal"] == "50.00"
    assert result["total"] == "60.00"


def test_rounding_uses_half_up() -> None:
    result = evaluate({"items": [{"name": "Дрібниця", "price": "10.005", "quantity": 1}]})

    assert result["subtotal"] == "10.01"
    assert result["tax"] == "2.00"
    assert result["total"] == "12.01"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "текст",
        None,
        {},
        {"items": []},
        {"items": "не масив"},
        {"items": [1]},
        {"items": [{"price": "1.00", "quantity": 1}]},
        {"items": [{"name": "", "price": "1.00", "quantity": 1}]},
        {"items": [{"name": "Товар", "price": 100, "quantity": 1}]},
        {"items": [{"name": "Товар", "price": "abc", "quantity": 1}]},
        {"items": [{"name": "Товар", "price": "NaN", "quantity": 1}]},
        {"items": [{"name": "Товар", "price": "-1.00", "quantity": 1}]},
        {"items": [{"name": "Товар", "price": "1.00"}]},
        {"items": [{"name": "Товар", "price": "1.00", "quantity": "1"}]},
        {"items": [{"name": "Товар", "price": "1.00", "quantity": -1}]},
        {"items": [{"name": "Товар", "price": "1.00", "quantity": True}]},
        {"items": [{"name": "Товар", "price": "1.00", "quantity": 1}], "loyalty_years": "2"},
        {"items": [{"name": "Товар", "price": "1.00", "quantity": 1}], "loyalty_years": -1},
        {"items": [{"name": "Товар", "price": "1.00", "quantity": 1}], "promo_code": 5},
        {"items": [{"name": "Товар", "price": "1.00", "quantity": 1}], "customer_type": 5},
        {"items": [{"name": "Товар", "price": "1.00", "quantity": 1}], "delivery": []},
    ],
)
def test_rejects_invalid_payload(payload: object) -> None:
    with pytest.raises(RequestError):
        evaluate(payload)
