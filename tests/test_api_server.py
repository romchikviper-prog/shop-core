"""Тести HTTP-маршрутів на справжньому локальному сокеті."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from shop.api.config import Settings
from shop.api.server import ShopHTTPServer, make_server


@pytest.fixture()
def server() -> Iterator[ShopHTTPServer]:
    instance = make_server(
        Settings(port=0, log_level="CRITICAL", max_body_bytes=1024),
        host="127.0.0.1",
    )
    instance.ready = True
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _request(
    server: ShopHTTPServer,
    method: str,
    path: str,
    body: object | bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_address[1],
        timeout=5,
    )
    headers: dict[str, str] = {}
    data: bytes | None
    if body is None:
        data = None
    elif isinstance(body, bytes):
        data = body
    else:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(data))
    try:
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()
    return response.status, payload


def test_healthz(server: ShopHTTPServer) -> None:
    status, payload = _request(server, "GET", "/healthz")

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["version"]
    assert payload["git_sha"]


def test_readyz(server: ShopHTTPServer) -> None:
    status, payload = _request(server, "GET", "/readyz")

    assert status == 200
    assert payload == {"status": "ready"}


def test_readyz_returns_503_before_ready(server: ShopHTTPServer) -> None:
    server.ready = False
    try:
        status, payload = _request(server, "GET", "/readyz")
    finally:
        server.ready = True

    assert status == 503
    assert payload == {"status": "not_ready"}


def test_evaluate(server: ShopHTTPServer) -> None:
    status, payload = _request(
        server,
        "POST",
        "/evaluate",
        {
            "items": [{"name": "Телефон", "price": "100.00", "quantity": 10}],
            "customer_type": "gold",
            "promo_code": "SAVE20",
            "delivery": "pickup",
        },
    )

    assert status == 200
    assert payload["total"] == "960.00"
    assert payload["promo_code"] == "SAVE20"


def test_evaluate_rejects_invalid_payload(server: ShopHTTPServer) -> None:
    status, payload = _request(server, "POST", "/evaluate", {"items": []})

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


def test_evaluate_rejects_broken_json(server: ShopHTTPServer) -> None:
    status, payload = _request(server, "POST", "/evaluate", b"{")

    assert status == 400
    assert payload["error"]["code"] == "invalid_json"


def test_evaluate_rejects_wrong_content_type(server: ShopHTTPServer) -> None:
    status, payload = _request(
        server,
        "POST",
        "/evaluate",
        {"items": []},
        content_type="text/plain",
    )

    assert status == 415
    assert payload["error"]["code"] == "unsupported_media_type"


def test_evaluate_rejects_too_large_body(server: ShopHTTPServer) -> None:
    status, payload = _request(server, "POST", "/evaluate", b"{" + b" " * 2048 + b"}")

    assert status == 413
    assert payload["error"]["code"] == "payload_too_large"


def test_evaluate_requires_body(server: ShopHTTPServer) -> None:
    status, payload = _request(server, "POST", "/evaluate")

    assert status == 400
    assert payload["error"]["code"] == "empty_body"


def test_unknown_get_route(server: ShopHTTPServer) -> None:
    status, payload = _request(server, "GET", "/unknown")

    assert status == 404
    assert payload["error"]["code"] == "not_found"


def test_unknown_post_route(server: ShopHTTPServer) -> None:
    status, payload = _request(server, "POST", "/unknown")

    assert status == 404
    assert payload["error"]["code"] == "not_found"
