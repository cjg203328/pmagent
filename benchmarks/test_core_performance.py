"""Explicit performance regression gate for the offline benchmark suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.core_performance import run_benchmarks


pytestmark = pytest.mark.benchmark


def test_core_performance_thresholds(tmp_path: Path) -> None:
    results = run_benchmarks(work_dir=tmp_path, samples=12, warmups=2)

    failures = [
        f"{result.name}: p95={result.p95_ms:.3f}ms "
        f"> {result.p95_limit_ms:.3f}ms"
        for result in results
        if not result.passed
    ]
    assert not failures, "performance regressions:\n" + "\n".join(failures)

