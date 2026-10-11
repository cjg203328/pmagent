"""Incremental mypy gate.

The project carries a large backlog of pre-existing type errors, so a plain
``mypy`` run cannot block a pull request without freezing all work. Instead this
gate records the current errors as a baseline and fails only when a change
introduces errors that are not in that baseline.

Comparison keys deliberately exclude line and column numbers: refactors move
code around constantly, and a line-number key would produce false failures. A
key is the file, the error code, and the message text.

Regenerate the baseline after intentionally fixing or accepting errors:

    python scripts/mypy_gate.py --update-baseline
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_FILE = REPO_ROOT / "mypy-baseline.txt"

TARGET = "artpm_agent"

# ``path:line: severity: message  [code]`` and the column-less variant that
# mypy emits for non-source files such as pyproject.toml.
_LOCATED = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): (?P<severity>error|warning|note): (?P<message>.*)$"
)
_UNLOCATED = re.compile(
    r"^(?P<path>.+?): (?P<severity>error|warning|note): (?P<message>.*)$"
)
_CODE = re.compile(r"\s*\[(?P<code>[a-z0-9-]+)\]\s*$")


def _normalize_path(raw: str) -> str:
    """Return a repository-relative POSIX path so CI and Windows agree."""
    cleaned = raw.strip().replace("\\", "/")
    root = REPO_ROOT.as_posix()
    if cleaned.startswith(root + "/"):
        cleaned = cleaned[len(root) + 1 :]
    return cleaned


def _parse_errors(output: str) -> list[str]:
    """Extract comparable keys for every ``error`` line in mypy output."""
    keys: list[str] = []
    for line in output.splitlines():
        match = _LOCATED.match(line) or _UNLOCATED.match(line)
        if match is None or match.group("severity") != "error":
            continue

        message = match.group("message").strip()
        code_match = _CODE.search(message)
        code = code_match.group("code") if code_match else "-"
        if code_match is not None:
            message = message[: code_match.start()].strip()

        keys.append(f"{_normalize_path(match.group('path'))}|{code}|{message}")
    return keys


def _run_mypy() -> tuple[int, str]:
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", TARGET, "--ignore-missing-imports"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def _load_baseline() -> set[str]:
    if not BASELINE_FILE.is_file():
        return set()
    return {
        line.strip()
        for line in BASELINE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def _write_baseline(keys: list[str]) -> None:
    header = (
        "# mypy incremental-gate baseline.\n"
        "# One '<file>|<code>|<message>' entry per accepted error.\n"
        "# Regenerate with: python scripts/mypy_gate.py --update-baseline\n"
    )
    body = "\n".join(sorted(set(keys)))
    BASELINE_FILE.write_text(f"{header}{body}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Record the current errors as the accepted baseline and exit.",
    )
    args = parser.parse_args(argv)

    returncode, output = _run_mypy()
    keys = _parse_errors(output)

    if args.update_baseline:
        _write_baseline(keys)
        print(f"Baseline updated: {len(set(keys))} accepted error(s) -> {BASELINE_FILE.name}")
        return 0

    if not keys and returncode == 0:
        print("mypy gate passed: no type errors.")
        return 0

    baseline = _load_baseline()
    if not baseline:
        print(
            "mypy gate has no baseline. Run "
            "`python scripts/mypy_gate.py --update-baseline` and commit the result.",
            file=sys.stderr,
        )
        return 1

    current = set(keys)
    new_errors = sorted(current - baseline)
    fixed_errors = sorted(baseline - current)

    for key in new_errors:
        path, code, message = key.split("|", 2)
        print(f"NEW {path} [{code}] {message}", file=sys.stderr)

    if fixed_errors:
        print(
            f"\nNote: {len(fixed_errors)} baseline error(s) no longer reported. "
            "Run --update-baseline to lock in the improvement.",
            file=sys.stderr,
        )

    if new_errors:
        print(
            f"\nmypy gate failed: {len(new_errors)} new type error(s) "
            f"beyond the {len(baseline)} accepted baseline entries.",
            file=sys.stderr,
        )
        return 1

    print(
        f"mypy gate passed: no new type errors "
        f"({len(current)} known error(s) remain within baseline)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
