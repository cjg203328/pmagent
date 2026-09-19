# Current Project Status

Validated on 2026-09-18 from branch
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
- RuntimeFactory scopes workflow/artifact runtimes by tenant, workspace and
  profile with bounded TTL/LRU cleanup; UI session state is only a compatibility
  alias.
- Canonical tool calls run through the Harness AgentLoop with JSON-schema
  validation, bounded turns/calls/workers, per-call timeout, cancellation,
  side-effect gates, scoped result spill and durable SessionStore events.

## Quality Commands

```powershell
python -m pytest -q --no-cov -m "not integration and not benchmark and not slow"
powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_integration.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_benchmark.ps1
powershell -ExecutionPolicy Bypass -File scripts/coverage_core.ps1
python scripts/mypy_ratchet.py
python scripts/build_graph.py --selftest
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
- Remote and local MCP blocking work is isolated from the host event loop;
  transport and tool return contracts remain backward compatible.
- API transcripts persist stable error codes rather than raw provider
  exceptions.
- Request dependencies are grouped in `RequestServiceBundle` while old
  `RequestOrchestrator` private methods remain compatibility delegates.
- `VectorBackend` defines the shared local/remote vector-store contract.
- Rule approval and rejection calls can be bound to an explicit workspace;
  older direct calls remain compatible when no workspace is supplied.
- `/ready` probes all authoritative Store contracts through `StorageRegistry`;
  optional model/OCR/MCP/vector capabilities are reported separately.
- API permission/workflow routes live in dedicated router modules; gateway and
  turn dependencies are expressed through Protocol ports. The remaining
  workspace/chat/embed/voice extraction is intentionally the next compatibility
  wave.

## Remaining External Verification

- PostgreSQL RLS requires a disposable PostgreSQL instance and a non-owner app
  role. Without one, the RLS integration test must remain an explicit skip.
- Live MCP verification requires `ART_ENABLE_INTEGRATION=1` and a valid
  Skills Forge configuration.
- Provider latency and failover need a controlled external model environment;
  offline tests must continue using fakes.
- The full-tree mypy ratchet baseline is 451 reviewed legacy diagnostics,
  measured with the pinned `mypy==2.3.1` command in `scripts/mypy_ratchet.py`.
  The previous 363 baseline predates the later memory/UI/API commits; 47
  diagnostics in this worktree's changed files were fixed before ratcheting.
  The four P2 packages (`runtime`, `harness`, `api`, `tenancy`) remain the
  strict zero-error gate, and the baseline only prevents further growth.
