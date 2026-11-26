"""Тест контракту: відповіді платіжного сервісу збігаються з OpenAPI."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import yaml

from shop.distributed.payment_service import PaymentServiceConfig
from tests.lr3_contract import validate
from tests.lr3_helpers import http_json, start_payment


ROOT = Path(__file__).resolve().parents[1]


def load_document() -> dict:
    with (ROOT / "openapi.yaml").open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_payment_responses_match_openapi_schema() -> None:
    document = load_document()
    schemas = document["components"]["schemas"]
    response_schema = schemas["PaymentResponse"]
    error_schema = schemas["ErrorResponse"]

    running = start_payment(
        PaymentServiceConfig(
            port=0,
            delay_seconds=0.0,
            slow_delay_seconds=0.0,
            error_fraction=1.0,
            seed=7,
        )
    )
    try:
        order_id = str(uuid4())
        key = f"order:{order_id}:payment"
        body = {"orderId": order_id, "amount": "19999.00", "currency": "UAH"}

        status, payload = http_json(
            "POST",
            running.base_url + "/payments",
            body,
            {"Idempotency-Key": key},
        )
        assert status == 201
        validate(payload, response_schema, document)
        assert payload["idempotentReplay"] is False
        payment_id = payload["paymentId"]

        status, replay = http_json(
            "POST",
            running.base_url + "/payments",
            body,
            {"Idempotency-Key": key},
        )
        assert status == 200
        validate(replay, response_schema, document)
        assert replay["paymentId"] == payment_id
        assert replay["idempotentReplay"] is True

        status, conflict = http_json(
            "POST",
            running.base_url + "/payments",
            {"orderId": order_id, "amount": "1.00", "currency": "UAH"},
            {"Idempotency-Key": key},
        )
        assert status == 409
        validate(conflict, error_schema, document)
        assert conflict["error"]["code"] == "idempotency_conflict"

        status, invalid = http_json(
            "POST",
            running.base_url + "/payments",
            body,
        )
        assert status == 400
        validate(invalid, error_schema, document)
        assert invalid["error"]["code"] == "invalid_request"

        status, bad_key = http_json(
            "POST",
            running.base_url + "/payments",
            body,
            {"Idempotency-Key": "not-an-order-key"},
        )
        assert status == 400
        validate(bad_key, error_schema, document)
        assert bad_key["error"]["code"] == "invalid_request"

        status, _state = http_json(
            "POST",
            running.base_url + "/_admin/mode",
            {"mode": "errors"},
        )
        assert status == 200

        other_order = str(uuid4())
        status, unavailable = http_json(
            "POST",
            running.base_url + "/payments",
            {
                "orderId": other_order,
                "amount": "10.00",
                "currency": "UAH",
            },
            {"Idempotency-Key": f"order:{other_order}:payment"},
        )
        assert status == 503
        validate(unavailable, error_schema, document)
        assert unavailable["error"]["code"] == "payment_unavailable"
    finally:
        running.close()
