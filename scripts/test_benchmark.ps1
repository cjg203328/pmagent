$ErrorActionPreference = "Stop"

python -m benchmarks.core_performance `
    --samples 12 `
    --warmups 2 `
    --output artifacts/benchmarks/core-performance.json `
    --enforce
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

python -m pytest -q --no-cov -m benchmark `
    benchmarks/test_core_performance.py `
    benchmarks/test_workspace_concurrency.py `
    tests/test_phase1_optimizations.py `
    @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
