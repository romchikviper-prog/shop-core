"""HTTP-сервер: маршрути, проби та коректне завершення.

Маршрути:

* ``GET /healthz`` — процес живий; відповідає навіть до готовності.
* ``GET /readyz`` — процес готовий приймати трафік.
* ``POST /evaluate`` — розрахунок ціни замовлення.

Liveness навмисно не перевіряє зовнішні залежності: збій бази не має
викликати каскад перезапусків усіх подів.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import urlsplit

from shop.api.config import Settings
from shop.api.service import RequestError, evaluate
from shop.api.version import GIT_SHA, VERSION

LOGGER = logging.getLogger("shop.api")


class ShopHTTPServer(ThreadingHTTPServer):
    """Сервер із налаштуваннями та прапорцем готовності."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        settings: Settings,
    ) -> None:
        super().__init__(address, handler)
        self.settings = settings
        self.ready = False


class ApiError(Exception):
    """Помилка, яку треба перетворити на HTTP-відповідь."""

    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class ApiHandler(BaseHTTPRequestHandler):
    """Обробник запитів; один екземпляр на з'єднання."""

    server_version = "shop-core"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def shop_server(self) -> ShopHTTPServer:
        return cast(ShopHTTPServer, self.server)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {"status": "ok", "version": VERSION, "git_sha": GIT_SHA},
            )
            return
        if path == "/readyz":
            self._handle_ready()
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Маршрут не знайдено")

    def _handle_ready(self) -> None:
        if self.shop_server.ready:
            self._send_json(HTTPStatus.OK, {"status": "ready"})
        else:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"status": "not_ready"},
            )

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/evaluate":
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Маршрут не знайдено")
            return
        try:
            payload = self._read_json()
            report = evaluate(payload)
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message)
            return
        except RequestError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
            return
        self._send_json(HTTPStatus.OK, report)

    def _read_json(self) -> object:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "empty_body", "Тіло запиту відсутнє")
        try:
            length = int(raw_length)
        except ValueError:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_length",
                "Content-Length має бути цілим числом",
            ) from None
        if length <= 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "empty_body", "Тіло запиту порожнє")
        if self.headers.get_content_type() != "application/json":
            raise ApiError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "unsupported_media_type",
                "Content-Type має бути application/json",
            )
        if length > self.shop_server.settings.max_body_bytes:
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "payload_too_large",
                f"Тіло запиту перевищує {self.shop_server.settings.max_body_bytes} байтів",
            )
        body = self.rfile.read(length)
        try:
            payload: object = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_json",
                f"Некоректний JSON: {exc}",
            ) from exc
        return payload

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            self.close_connection = True

    def _send_error(self, status: HTTPStatus, code: str, message: str) -> None:
        # Тіло помилки могло не бути прочитане (наприклад, 413), тому
        # з'єднання закривається: інакше залишок тіла стане наступним
        # «запитом» і зламає HTTP/1.1 keep-alive.
        self.close_connection = True
        self._send_json(
            status,
            {"error": {"code": code, "message": message}},
        )

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s %s", self.address_string(), format % args)


def make_server(settings: Settings, host: str = "0.0.0.0") -> ShopHTTPServer:
    """Створює сервер; для тестів можна передати host=127.0.0.1 і port=0."""

    return ShopHTTPServer((host, settings.port), ApiHandler, settings)
