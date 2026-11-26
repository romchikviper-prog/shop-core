"""Інтеграційні тести сервісу «Ядро» через реальний HTTP."""

from __future__ import annotations

from uuid import uuid4

from shop.distributed.core_service import CoreConfig
from shop.distributed.payment_service import PaymentServiceConfig
from tests.lr3_helpers import (
    free_port,
    http_json,
    new_customer_id,
    new_order_id,
    order_line_json,
    start_core,
    start_payment,
)


def place_body(order_id: str, customer_id: str, command_id: str) -> dict:
    return {
        "commandId": command_id,
        "type": "PlaceOrder",
        "orderId": order_id,
        "customerId": customer_id,
        "lines": [order_line_json()],
    }


def test_core_duplicate_fallback_and_replay(tmp_path) -> None:
    dead_port = free_port()
    running = start_core(
        CoreConfig(
            port=0,
            event_log=tmp_path / "events.jsonl",
            checkpoint=tmp_path / "projection.json",
            payment_url=f"http://127.0.0.1:{dead_port}",
            client_mode="protected",
            request_timeout=0.5,
            attempt_timeout=0.1,
            retry_attempts=3,
            retry_base_delay=0.001,
            retry_max_delay=0.002,
            breaker_threshold=2,
            breaker_open_seconds=10.0,
            bulkhead_limit=2,
            projection_poll_seconds=0.01,
        )
    )
    try:
        order_id = str(new_order_id())
        customer_id = str(new_customer_id())
        command_id = str(uuid4())
        body = place_body(order_id, customer_id, command_id)

        status, placed = http_json(
            "POST", running.base_url + "/commands", body
        )
        assert status == 200
        assert placed["status"] == "applied"
        assert placed["orderStatus"] == "placed"
        assert placed["version"] == 1

        status, duplicate = http_json(
            "POST", running.base_url + "/commands", body
        )
        assert status == 200
        assert duplicate["status"] == "duplicate"
        assert duplicate["version"] == 1
        assert duplicate["events"] == ["OrderPlaced"]

        status, state = http_json(
            "GET", running.base_url + f"/orders/{order_id}"
        )
        assert status == 200
        assert state["status"] == "placed"
        assert state["version"] == 1

        status, degraded = http_json(
            "POST",
            running.base_url + "/commands",
            {
                "commandId": str(uuid4()),
                "type": "PayOrder",
                "orderId": order_id,
            },
        )
        assert status == 200
        assert degraded["status"] == "degraded"
        assert degraded["degraded"] is True
        assert degraded["orderStatus"] == "placed"
        assert degraded["error"]["code"] in ("circuit_open", "transport_error")

        status, stats = http_json("GET", running.base_url + "/stats")
        assert status == 200
        assert stats["paymentClient"]["breakerState"] == "open"
        assert stats["metrics"]["commandsDegraded"] == 1
        assert any(
            item["from"] == "closed" and item["to"] == "open"
            for item in stats["paymentClient"]["transitions"]
        )

        status, replay = http_json(
            "POST", running.base_url + "/projection/replay"
        )
        assert status == 200
        assert replay["checkpoint"] == 1
        checksum = replay["checksum"]
        status, replay_again = http_json(
            "POST", running.base_url + "/projection/replay"
        )
        assert status == 200
        assert replay_again["checksum"] == checksum

        status, projection = http_json(
            "GET", running.base_url + "/projection/orders?status=placed"
        )
        assert status == 200
        assert [item["orderId"] for item in projection["orders"]] == [order_id]

        status, reset = http_json(
            "POST", running.base_url + "/admin/reset"
        )
        assert status == 200
        assert reset["paymentClient"]["breakerState"] == "closed"
    finally:
        running.close()


def test_core_payment_end_to_end_and_idempotency(tmp_path) -> None:
    payment = start_payment(
        PaymentServiceConfig(
            port=0,
            delay_seconds=0.0,
            slow_delay_seconds=0.0,
            error_fraction=0.0,
            seed=3,
        )
    )
    core = start_core(
        CoreConfig(
            port=0,
            event_log=tmp_path / "events.jsonl",
            checkpoint=tmp_path / "projection.json",
            payment_url=payment.base_url,
            client_mode="protected",
            request_timeout=1.0,
            attempt_timeout=0.5,
            retry_attempts=2,
            retry_base_delay=0.001,
            retry_max_delay=0.002,
            breaker_threshold=3,
            breaker_open_seconds=2.0,
            bulkhead_limit=2,
            projection_poll_seconds=0.01,
        )
    )
    try:
        order_id = str(new_order_id())
        customer_id = str(new_customer_id())
        status, placed = http_json(
            "POST",
            core.base_url + "/commands",
            place_body(order_id, customer_id, str(uuid4())),
        )
        assert status == 200
        assert placed["status"] == "applied"

        pay_command = str(uuid4())
        pay_body = {
            "commandId": pay_command,
            "type": "PayOrder",
            "orderId": order_id,
        }
        status, paid = http_json(
            "POST", core.base_url + "/commands", pay_body
        )
        assert status == 200
        assert paid["status"] == "applied"
        assert paid["orderStatus"] == "paid"
        assert paid["version"] == 2
        assert paid["events"] == ["PaymentCaptured"]

        status, duplicate = http_json(
            "POST", core.base_url + "/commands", pay_body
        )
        assert status == 200
        assert duplicate["status"] == "duplicate"
        assert duplicate["version"] == 2

        status, payment_state = http_json(
            "GET", payment.base_url + "/_admin/state"
        )
        assert status == 200
        assert payment_state["stats"]["total"] == 1

        status, state = http_json(
            "GET", core.base_url + f"/orders/{order_id}"
        )
        assert status == 200
        assert state["status"] == "paid"

        status, cancelled = http_json(
            "POST",
            core.base_url + "/commands",
            {
                "commandId": str(uuid4()),
                "type": "CancelOrder",
                "orderId": order_id,
                "reason": "клієнт передумав",
            },
        )
        assert status == 409
        assert cancelled["error"]["code"] == "invalid_state"

        status, replay = http_json(
            "POST", core.base_url + "/projection/replay"
        )
        assert status == 200
        assert replay["checkpoint"] == 2

        status, projection = http_json(
            "GET", core.base_url + "/projection/orders?status=paid"
        )
        assert status == 200
        assert [item["orderId"] for item in projection["orders"]] == [order_id]
    finally:
        core.close()
        payment.close()
