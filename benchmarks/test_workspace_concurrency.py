from __future__ import annotations

import pytest

from benchmarks.workspace_concurrency import run_workspace_concurrency_benchmark

pytestmark = pytest.mark.benchmark


def test_distinct_workspace_chats_run_concurrently(tmp_path):
    result = run_workspace_concurrency_benchmark(
        work_dir=tmp_path,
        workspaces=6,
        delay_seconds=0.05,
    )

    assert result.passed, (
        f"workspace chat path serialized: elapsed={result.elapsed_seconds:.4f}s "
        f"serial={result.serial_seconds:.4f}s speedup={result.speedup:.2f}x"
    )
