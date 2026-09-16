# Current Project Status

Validated on 2026-09-12 from branch
`chore/consolidate-uncommitted-work`.

## Runtime Shape

- Streamlit remains the primary local UI.
- FastAPI is the authenticated REST gateway.
- `ArtPMAgent` is a compatibility facade over `RequestOrchestrator`.
- `LocalHarnessRuntime` owns request-scoped services and tenant binding;
  `run_turn()` is the canonical request-processing boundary for API, UI and
  CLI turn execution.
- `HarnessRuntime` is the provider-neutral contract and
  `LegacyAgentRuntimeAdapter` is compatibility-only.
- SQLite and FAISS are the offline defaults. PostgreSQL, Qdrant, Redis,
  telemetry, and Sentry are deployment-selected integrations.

## Quality Commands

```powershell
python -m pytest -q --no-cov -m "not integration and not benchmark and not slow"
powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_integration.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_benchmark.ps1
powershell -ExecutionPolicy Bypass -File scripts/coverage_core.ps1
# Full-repository blocking correctness rules
ruff check artpm_agent tests --select E9,F63,F7,F82

# Changed Python files also run the normal project rule set
ruff check <changed-python-files>
```

The fast suite excludes external integrations, benchmarks, and expensive
Streamlit startup tests. The offline suite includes the slow UI tests. Coverage
is a separate focused 90% gate over the modernization boundary modules;
integration tests require explicit credentials or services and must never be
treated as offline tests.

Unrestricted whole-repository Ruff still reports historical modernization and
style debt. It is governed as a changed-surface ratchet: new or edited focused
modules must pass the normal rule set, while the repository-wide blocking gate
always covers syntax errors, invalid constructs, and undefined names.

## Current Boundaries

- API chat is an async route. Native async handlers can be injected through
  `GatewayServices.chat_async_handler`; the legacy synchronous handler runs in a
  worker thread until provider clients are migrated.
- API transcripts persist stable error codes rather than raw provider
  exceptions.
- Request dependencies are grouped in `RequestServiceBundle` while old
  `RequestOrchestrator` private methods remain compatibility delegates.
- `VectorBackend` defines the shared local/remote vector-store contract.
- Rule approval and rejection calls can be bound to an explicit workspace;
  older direct calls remain compatible when no workspace is supplied.

## Remaining External Verification

- PostgreSQL RLS requires a disposable PostgreSQL instance and a non-owner app
  role. Without one, the RLS integration test must remain an explicit skip.
- Live MCP verification requires `ART_ENABLE_INTEGRATION=1` and a valid
  Skills Forge configuration.
- Provider latency and failover need a controlled external model environment;
  offline tests must continue using fakes.
