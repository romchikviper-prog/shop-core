"""Тести читання конфігурації зі змінних середовища."""

from __future__ import annotations

import pytest

from shop.api.config import Settings


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SHOP_PORT", "SHOP_LOG_LEVEL", "SHOP_MAX_BODY_BYTES"):
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)

    settings = Settings.from_env()

    assert settings == Settings(port=8080, log_level="INFO", max_body_bytes=65536)


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("SHOP_PORT", "9090")
    monkeypatch.setenv("SHOP_LOG_LEVEL", "debug")
    monkeypatch.setenv("SHOP_MAX_BODY_BYTES", "2048")

    settings = Settings.from_env()

    assert settings == Settings(port=9090, log_level="DEBUG", max_body_bytes=2048)


def test_empty_values_mean_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("SHOP_PORT", "")
    monkeypatch.setenv("SHOP_LOG_LEVEL", "  ")
    monkeypatch.setenv("SHOP_MAX_BODY_BYTES", " ")

    settings = Settings.from_env()

    assert settings == Settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SHOP_PORT", "abc"),
        ("SHOP_PORT", "0"),
        ("SHOP_PORT", "70000"),
        ("SHOP_MAX_BODY_BYTES", "abc"),
        ("SHOP_MAX_BODY_BYTES", "10"),
        ("SHOP_MAX_BODY_BYTES", str(11 * 1024 * 1024)),
        ("SHOP_LOG_LEVEL", "VERBOSE"),
    ],
)
def test_rejects_invalid_environment(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        Settings.from_env()
