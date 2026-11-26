"""Архітектурні інваріанти: напрямок залежностей між шарами."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "shop"
IMPORT = re.compile(r"^(?:from|import)\s+([\w.]+)", re.MULTILINE)


def imports_of(path: Path) -> set[str]:
    return set(IMPORT.findall(path.read_text(encoding="utf-8")))


def test_domain_does_not_depend_on_outer_layers() -> None:
    for path in (SRC / "domain").glob("*.py"):
        modules = imports_of(path)
        assert not any(module.startswith("shop.application") for module in modules), path
        assert not any(module.startswith("shop.infrastructure") for module in modules), path


def test_application_does_not_depend_on_infrastructure() -> None:
    for path in (SRC / "application").glob("*.py"):
        modules = imports_of(path)
        assert not any(module.startswith("shop.infrastructure") for module in modules), path
