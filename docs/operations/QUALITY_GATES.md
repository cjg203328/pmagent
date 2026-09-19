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

# Type and architecture governance
python scripts/mypy_ratchet.py
python scripts/build_graph.py --selftest
python -m pytest -q tests/test_ci_contract.py tests/test_p2_module_boundaries.py --no-cov
```

The default `pytest` command runs functional tests without coverage overhead.
The focused gate enforces at least 90% coverage over the runtime, cache, vector,
component, and tenant-session boundary modules. A whole-project HTML report is
available through `scripts/coverage_report.sh` when needed, but it is not a
per-commit gate because UI startup tests make that signal too slow and noisy.
CI also runs a separate whole-project baseline job at 20%. This low threshold
is intentional during the decomposition of the legacy UI and knowledge-store
modules; it must be raised as those modules gain focused tests.

Tests under `tests/integration/` are automatically marked `integration`.
Streamlit and model synchronization modules with expensive application startup
are marked `slow`. Marking is applied during collection so existing tests keep
their normal names and can still be selected directly.

The type ratchet has two layers. The full package keeps a reviewed diagnostic
ceiling so legacy debt cannot grow; `runtime/`, `harness/`, `api/` and
`tenancy/` must independently pass strict mypy with
`--ignore-missing-imports --follow-imports=silent`. The architecture tests
enforce physical router/store splits and Protocol-owned service fields. The
dependency graph is only an impact-analysis accelerator; `rg`, tests and type
checks remain authoritative.
