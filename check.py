"""Одна команда для перевірки типів і запуску тестів.

Використання:
    py -3 check.py

Скрипт послідовно запускає mypy --strict для src/ і pytest.
Якщо хоч одна перевірка не пройшла, повертає ненульовий код виходу.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = os.environ.copy()
ENV["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + ENV.get("PYTHONPATH", "")
ENV["PYTHONIOENCODING"] = "utf-8"


def run(label: str, args: list[str]) -> int:
    print(f"\n=== {label} ===", flush=True)
    print("$", " ".join(args), flush=True)
    return subprocess.call(args, cwd=ROOT, env=ENV)


def main() -> int:
    mypy_code = run("mypy --strict", [sys.executable, "-m", "mypy", "--strict", "src"])
    pytest_code = run("pytest", [sys.executable, "-m", "pytest"])
    if mypy_code != 0 or pytest_code != 0:
        print("\nПЕРЕВІРКА НЕ ПРОЙДЕНА", flush=True)
        return 1
    print("\nУСІ ПЕРЕВІРКИ ПРОЙДЕНІ", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
