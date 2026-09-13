"""Run the focused modernization coverage gate on every platform."""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path


TEST_FILES = (
    "tests/test_architecture_modernization.py",
    "tests/test_response_cache.py",
    "tests/test_agent_runtime.py",
    "tests/test_vector_search.py",
)

INCLUDE = (
    "artpm_agent/agent.py,"
    "artpm_agent/components.py,"
    "artpm_agent/database/tenant_session.py,"
    "artpm_agent/memory/vector_store.py,"
    "artpm_agent/providers/response_cache.py"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
QUALITY_DIR = REPO_ROOT / "artifacts" / "quality"
COVERAGE_FILE = QUALITY_DIR / ".coverage"


def main() -> int:
    QUALITY_DIR.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["COVERAGE_FILE"] = str(COVERAGE_FILE)
    pytest_command = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        *TEST_FILES,
        "--cov=artpm_agent",
        "--cov-report=",
        f"--cov-report=xml:{QUALITY_DIR / 'coverage.xml'}",
    ]
    completed = subprocess.run(
        pytest_command,
        check=False,
        cwd=REPO_ROOT,
        env=environment,
    )
    if completed.returncode:
        return completed.returncode
    coverage_command = [
        sys.executable,
        "-m",
        "coverage",
        "report",
        f"--include={INCLUDE}",
        "--show-missing",
        "--fail-under=90",
    ]
    return subprocess.run(
        coverage_command,
        check=False,
        cwd=REPO_ROOT,
        env=environment,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
