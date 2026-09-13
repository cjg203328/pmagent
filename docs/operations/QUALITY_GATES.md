# Quality Gates

The test suite is intentionally split by execution cost and external
dependencies. The default full offline command remains available, while fast
feedback is explicit and does not start UI or external-service tests.

## Commands

```powershell
# Fast local regression: deterministic unit and contract tests
powershell -ExecutionPolicy Bypass -File scripts/test_fast.ps1

# Complete offline suite, including Streamlit UI tests
powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1

# Opt-in external integration contracts
powershell -ExecutionPolicy Bypass -File scripts/test_integration.ps1

# Offline performance gates
powershell -ExecutionPolicy Bypass -File scripts/test_benchmark.ps1

# Focused coverage gate for modernization boundary modules
powershell -ExecutionPolicy Bypass -File scripts/coverage_core.ps1

# Cross-platform equivalent
python scripts/coverage_core.py
```

The default `pytest` command runs functional tests without coverage overhead.
The focused gate enforces at least 90% coverage over the runtime, cache, vector,
component, and tenant-session boundary modules. A whole-project HTML report is
available through `scripts/coverage_report.sh` when needed, but it is not a
per-commit gate because UI startup tests make that signal too slow and noisy.

Tests under `tests/integration/` are automatically marked `integration`.
Streamlit and model synchronization modules with expensive application startup
are marked `slow`. Marking is applied during collection so existing tests keep
their normal names and can still be selected directly.
