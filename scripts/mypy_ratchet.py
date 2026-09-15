"""Run the staged mypy gate and reject new repository-wide diagnostics.

The full tree still contains legacy typing debt, so its reviewed diagnostic
count is kept as a ratchet. A small set of boundary modules has already been
cleaned up and is checked with ``--strict`` on every CI run. Keep this module
list explicit: adding a module is a deliberate typing-migration milestone.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "mypy-baseline.txt"
ERROR_RE = re.compile(r"^.+:\d+: error:")
STAGED_MODULES = (
    "artpm_agent/harness/agent_session.py",
    "artpm_agent/harness/artifact_handler.py",
    "artpm_agent/harness/attachment_pipeline.py",
    "artpm_agent/harness/knowledge_rule_handler.py",
    "artpm_agent/harness/model_handler.py",
    "artpm_agent/harness/profile_handler.py",
    "artpm_agent/harness/workflow_handler.py",
    "artpm_agent/runtime/agent_runtime.py",
    "artpm_agent/runtime/plan.py",
    "artpm_agent/runtime/request_services.py",
    "artpm_agent/runtime/tools.py",
    "artpm_agent/runtime/turn_events.py",
    "artpm_agent/tenancy/context.py",
    "artpm_agent/security/access_mode.py",
    "artpm_agent/security/permission_gate.py",
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

    staged = _run_mypy(
        [
            *STAGED_MODULES,
            "--strict",
            "--ignore-missing-imports",
            "--follow-imports=skip",
            "--no-pretty",
            "--show-error-codes",
            "--no-incremental",
        ]
    )
    if staged.returncode != 0:
        print("mypy strict staged modules failed:", file=sys.stderr)
        print(_output(staged), file=sys.stderr, end="")
        return 1
    print(f"mypy strict staged modules: {len(STAGED_MODULES)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
