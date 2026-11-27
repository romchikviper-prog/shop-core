"""Генерує ``src/shop/_build_info.py`` з хешем коміту.

Використання:
    python scripts/write_build_info.py [sha]

Без аргументу хеш береться з ``git rev-parse --short=7 HEAD``. Файл
ігнорується git і потрапляє і в wheel, і в образ, тому версія в
``/healthz`` завжди відповідає коміту, з якого зібрано артефакт.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "shop" / "_build_info.py"
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{4,40}$")


def _git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "dev"
    return completed.stdout.strip() or "dev"


def _validate(sha: str) -> str:
    if sha != "dev" and not SHA_PATTERN.fullmatch(sha):
        raise SystemExit(f"Некоректний хеш коміту: {sha!r}")
    return sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", nargs="?", help="хеш коміту; за замовчуванням — git HEAD")
    args = parser.parse_args()

    sha = _validate(args.sha or _git_sha())
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        f'"""Згенеровано scripts/write_build_info.py; не редагувати."""\n\nGIT_SHA = "{sha}"\n',
        encoding="utf-8",
    )
    print(f"build info: {sha} -> {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
