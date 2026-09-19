from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_benchmark_gate_covers_p95_concurrency_and_vector_paths():
    script = (ROOT / "scripts" / "test_benchmark.ps1").read_text(encoding="utf-8")

    for value in (
        "python -m benchmarks.core_performance",
        "--samples 12",
        "--warmups 2",
        "--output artifacts/benchmarks/core-performance.json",
        "--enforce",
        "benchmarks/test_core_performance.py",
        "benchmarks/test_workspace_concurrency.py",
        "tests/test_phase1_optimizations.py",
        "-m benchmark",
    ):
        assert value in script


def test_local_benchmark_gate_propagates_each_native_failure():
    script = (ROOT / "scripts" / "test_benchmark.ps1").read_text(encoding="utf-8")

    assert script.count("if ($LASTEXITCODE -ne 0) {") == 2
    assert script.count("exit $LASTEXITCODE") == 2
