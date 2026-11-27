"""Версія збірки, вшита в пакет під час збирання образу.

Файл `shop/_build_info.py` генерує `scripts/write_build_info.py` (його
викликають `make build` і Dockerfile). У репозиторії цього файлу немає,
тому локальний запуск без збирання чесно показує версію `dev`.
"""

from __future__ import annotations

from importlib import import_module

BASE_VERSION = "0.1.0"
DEV_VERSION = "dev"


def _git_sha() -> str:
    try:
        module = import_module("shop._build_info")
    except ModuleNotFoundError:
        return DEV_VERSION
    value = getattr(module, "GIT_SHA", DEV_VERSION)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEV_VERSION


GIT_SHA = _git_sha()
VERSION = BASE_VERSION if GIT_SHA == DEV_VERSION else f"{BASE_VERSION}+{GIT_SHA}"
