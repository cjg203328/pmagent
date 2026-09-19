"""Run the mypy ratchet and strict checks for the governed packages.

The full tree still contains legacy typing debt, so its reviewed diagnostic
count remains a ratchet. Runtime, Harness, API, and tenancy are P2-governed
boundaries: every module in those packages must pass strict mypy. Imported
legacy modules are followed silently so they remain typed without expanding
the strict gate beyond its declared package roots.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "mypy-baseline.txt"
ERROR_RE = re.compile(r"^.+:\d+: error:")
STRICT_PACKAGES = (
    "artpm_agent/runtime",
    "artpm_agent/harness",
    "artpm_agent/api",
    "artpm_agent/tenancy",
)
STRICT_OPTIONS = (
    "--strict",
    "--ignore-missing-imports",
    "--follow-imports=silent",
    "--no-pretty",
    "--show-error-codes",
    "--no-incremental",
)


def _run_mypy(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mypy", *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _output(process: subprocess.CompletedProcess[str]) -> str:
    return process.stdout + process.stderr


def _error_count(output: str) -> int:
    return sum(1 for line in output.splitlines() if ERROR_RE.match(line))


def main() -> int:
    try:
        baseline = int(BASELINE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        print(f"mypy ratchet baseline is invalid: {error}", file=sys.stderr)
        return 2

    full = _run_mypy(
        [
            "artpm_agent",
            "--ignore-missing-imports",
            "--no-pretty",
            "--show-error-codes",
            "--no-incremental",
        ]
    )
    output = _output(full)
    current = _error_count(output)
    print(f"mypy diagnostics: {current} (baseline: {baseline})")
    # A non-standard exit without any parsed diagnostics indicates a config,
    # import, or tool crash rather than reviewed legacy debt.
    if full.returncode not in (0, 1) or (full.returncode != 0 and current == 0):
        print(output, file=sys.stderr, end="")
        print("mypy full-tree invocation failed", file=sys.stderr)
        return 2
    if current > baseline:
        print("mypy ratchet failed: diagnostics increased", file=sys.stderr)
        return 1
    if current < baseline:
        print("mypy baseline can be lowered after review")

    strict = _run_mypy([*STRICT_PACKAGES, *STRICT_OPTIONS])
    if strict.returncode != 0:
        print("mypy strict package gate failed:", file=sys.stderr)
        print(_output(strict), file=sys.stderr, end="")
        return 1
    print(f"mypy strict packages: {len(STRICT_PACKAGES)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
