"""Налаштування сервісу зі змінних середовища.

Дванадцятифакторний підхід: конфігурація приходить із середовища, а не
з образу. У Kubernetes змінні дає ConfigMap, у локальному запуску — .env
або параметри оболонки.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_PORT = 8080
DEFAULT_MAX_BODY_BYTES = 64 * 1024
DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Перевірена конфігурація процесу."""

    port: int = DEFAULT_PORT
    log_level: str = DEFAULT_LOG_LEVEL
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES

    @classmethod
    def from_env(cls) -> Settings:
        """Читає налаштування зі змінних SHOP_* і перевіряє їх.

        Помилка конфігурації не ігнорується: процес не стартує, тому
        несправний под видно в kubectl logs, а не в дивній поведінці.
        """

        return cls(
            port=_int_env("SHOP_PORT", DEFAULT_PORT, minimum=1, maximum=65535),
            log_level=_log_level(),
            max_body_bytes=_int_env(
                "SHOP_MAX_BODY_BYTES",
                DEFAULT_MAX_BODY_BYTES,
                minimum=1024,
                maximum=10 * 1024 * 1024,
            ),
        )


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} має бути цілим числом, отримано {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} має бути в межах {minimum}..{maximum}, отримано {value}")
    return value


def _log_level() -> str:
    value = os.environ.get("SHOP_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
    if not value:
        return DEFAULT_LOG_LEVEL
    if value not in LOG_LEVELS:
        allowed = ", ".join(sorted(LOG_LEVELS))
        raise ValueError(f"SHOP_LOG_LEVEL має бути одним із {allowed}, отримано {value!r}")
    return value
