"""Перевіряє цикломатичну складність функцій сервісу.

Поріг — 10, як у ЛР4. Перевіряються модулі, які входять у HTTP-сервіс:
``src/shop/api`` і спадковий розрахунок ціни ``src/shop/legacy``.
Модулі ЛР2–ЛР3 не є частиною цього сервісу й мають власні виміри.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = ("src/shop/api", "src/shop/legacy")
DEFAULT_MAX = 10


def _radon(paths: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-m", "radon", "cc", "-j", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"radon завершився з кодом {completed.returncode}:\n{completed.stderr}")
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"radon повернув некоректний JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("radon повернув неочікувану структуру JSON")
    return data


def _violations(data: dict[str, Any], maximum: int) -> list[str]:
    result: list[str] = []
    for path, blocks in data.items():
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            complexity = block.get("complexity")
            if isinstance(complexity, int) and complexity > maximum:
                lineno = block.get("lineno", "?")
                name = block.get("name", "?")
                result.append(f"{path}:{lineno} {name} — CC {complexity} > {maximum}")
    return result


def _summary(data: dict[str, Any]) -> str:
    values = [
        block["complexity"]
        for blocks in data.values()
        if isinstance(blocks, list)
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("complexity"), int)
    ]
    maximum = max(values) if values else 0
    return f"функцій: {len(values)}, максимальна CC: {maximum}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, help="гранична CC (типово 10)")
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS), help="теки для перевірки")
    args = parser.parse_args()

    data = _radon(args.paths)
    violations = _violations(data, args.max)
    print(_summary(data))
    if violations:
        for violation in violations:
            print(f"ПОРУШЕННЯ: {violation}")
        return 1
    print(f"Усі функції мають CC <= {args.max}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
