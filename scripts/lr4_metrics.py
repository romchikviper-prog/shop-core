"""Знімання метрик ЛР4 до і після рефакторингу.

Використання:
    py -3 scripts/lr4_metrics.py --label before
    py -3 scripts/lr4_metrics.py --label after

Скрипт запускає ті самі інструменти з тими самими налаштуваннями:
radon (складність і довжина), coverage.py --branch (покриття) і ruff
(зауваження лінтера). Результат — docs/lr4/metrics_<label>.json.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "shop" / "legacy" / "order_pricing.py"
TARGET_REL = TARGET.relative_to(ROOT).as_posix()
RUFF_SELECT = "C901,PLR0912,PLR0915,PLR2004,PLR0913"


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=check,
    )


def radon_metrics() -> dict[str, Any]:
    result = run([sys.executable, "-m", "radon", "cc", "-j", TARGET_REL])
    data = json.loads(result.stdout)
    blocks = data[TARGET_REL]
    complexities = [int(block["complexity"]) for block in blocks]
    functions = [block for block in blocks if block["type"] == "function"]
    return {
        "blocks": len(blocks),
        "functions": len(functions),
        "max_cyclomatic_complexity": max(complexities),
        "average_cyclomatic_complexity": round(statistics.mean(complexities), 2),
        "function_complexities": {
            str(block["name"]): int(block["complexity"]) for block in functions
        },
    }


def length_metrics() -> dict[str, Any]:
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    longest = max(
        functions,
        key=lambda node: (node.end_lineno or node.lineno) - node.lineno + 1,
    )
    return {
        "longest_function": longest.name,
        "longest_function_lines": (longest.end_lineno or longest.lineno)
        - longest.lineno
        + 1,
        "function_lengths": {
            node.name: (node.end_lineno or node.lineno) - node.lineno + 1
            for node in functions
        },
    }


def raw_metrics() -> dict[str, Any]:
    result = run([sys.executable, "-m", "radon", "raw", "-j", TARGET_REL])
    data = json.loads(result.stdout)
    raw = data[TARGET_REL]
    return {"loc": int(raw["loc"]), "sloc": int(raw["sloc"])}


def coverage_metrics() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / "coverage.json"
        run([sys.executable, "-m", "coverage", "erase"])
        run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--branch",
                "--source=shop.legacy.order_pricing",
                "-m",
                "pytest",
                "-q",
                "-k",
                "legacy_pricing",
                "tests",
            ]
        )
        run(
            [
                sys.executable,
                "-m",
                "coverage",
                "json",
                "--pretty-print",
                "-o",
                str(json_path),
            ]
        )
        data = json.loads(json_path.read_text(encoding="utf-8"))
    summary = next(
        file_data["summary"]
        for name, file_data in data["files"].items()
        if name.replace("\\", "/").endswith(TARGET_REL)
    )
    branches = int(summary["num_branches"])
    covered_branches = int(summary["covered_branches"])
    return {
        "coverage_lines_percent": round(
            int(summary["covered_lines"]) / int(summary["num_statements"]) * 100, 2
        ),
        "coverage_branches_percent": round(
            covered_branches / branches * 100, 2
        )
        if branches
        else 100.0,
        "lines_total": int(summary["num_statements"]),
        "lines_covered": int(summary["covered_lines"]),
        "branches_total": branches,
        "branches_covered": covered_branches,
    }


def linter_metrics() -> dict[str, Any]:
    result = run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            RUFF_SELECT,
            "--output-format",
            "json",
            TARGET_REL,
        ],
        check=False,
    )
    warnings = json.loads(result.stdout) if result.stdout.strip() else []
    return {
        "linter_warnings": len(warnings),
        "rules": [
            {
                "code": warning.get("code"),
                "message": warning.get("message"),
                "line": warning.get("location", {}).get("row"),
            }
            for warning in warnings
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, choices=["before", "after"])
    args = parser.parse_args()

    metrics = {
        "label": args.label,
        "target": TARGET_REL,
        "tools": {
            "complexity": "radon 6.0.1 (cc -j)",
            "length": "ast (фізичні рядки функції)",
            "coverage": "coverage.py 7.17.5 (--branch)",
            "linter": "ruff 0.17.6 (C901, PLR0912, PLR0915, PLR2004, PLR0913)",
        },
        **radon_metrics(),
        **length_metrics(),
        **raw_metrics(),
        **coverage_metrics(),
        **linter_metrics(),
    }
    output = ROOT / "docs" / "lr4" / f"metrics_{args.label}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"\nЗаписано: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
