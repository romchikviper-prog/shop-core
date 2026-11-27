"""Точка входу сервісу: ``python -m shop.api``."""

from __future__ import annotations

import logging
import signal
import threading
from types import FrameType

from shop.api.config import Settings
from shop.api.server import make_server
from shop.api.version import VERSION

LOGGER = logging.getLogger("shop.api")


def main() -> int:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = make_server(settings)

    def request_stop(signum: int, _frame: FrameType | None) -> None:
        LOGGER.info("Отримано сигнал %s, завершую роботу", signum)
        server.ready = False
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    server.ready = True
    LOGGER.info("shop-core %s слухає на порту %s", VERSION, settings.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        LOGGER.info("Сервіс зупинено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
