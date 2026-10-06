# Current Project Status

Validated on 2026-10-06 from branch `main`.

## Runtime Shape

- React is the new primary UI (`frontend/`, port `1501`), served by Vite and
  talking to the FastAPI gateway. Streamlit (`8501`) remains available.
- FastAPI is the authenticated REST gateway.
- `ArtPMAgent` is a compatibility facade over `RequestOrchestrator`.
- `HarnessRuntime` and `run_turn()` are the canonical request-processing
  boundary for API and UI turn execution.
- SQLite and FAISS are the offline defaults. PostgreSQL, Qdrant, Redis,
  telemetry, and Sentry are deployment-selected integrations.

## Conversation Access Modes

Each conversation persists one approval policy, enforced by the host rather than
the model. `controlled` is the fail-closed baseline: a missing, expired, or
differently-bound grant always falls back to it.

| Mode | Semantics | TTL |
| --- | --- | --- |
| `read_only` | Pure reads pass; writes are denied outright with no approval queued | 7 days |
| `controlled` | Every write is confirmed individually (default) | not persisted |
| `full_access` | Trusted low/medium-risk actions auto-approve; high risk still confirms | 1 hour |

Grants live in `data/conversations.db` (`conversation_access_grants`, permission
schema v4). The gateway injects the persisted mode into the execution context in
`DefaultGatewayRuntime._conversation_access_mode`; the model cannot widen it.

## Gateway Configuration API

`GET/PUT /v1/config` exposes an allowlisted `.env` schema so the React settings
page can manage provider credentials without shell access. Secrets are never
returned (only `configured` plus a masked preview), writes are validated as a
whole batch before anything lands, comments/order/line-endings are preserved,
and the runtime is hot-reloaded afterwards. Both routes require the `admin`
role.

## Quality Commands

```powershell
python -m pytest -q --no-cov -m "not integration and not benchmark and not slow"
powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_integration.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_benchmark.ps1
powershell -ExecutionPolicy Bypass -File scripts/coverage_core.ps1
ruff check artpm_agent tests
```

Frontend gates (run in `frontend/`):

```powershell
pnpm typecheck
pnpm test
pnpm build
```

The fast suite excludes external integrations, benchmarks, and expensive
Streamlit startup tests. The offline suite includes the slow UI tests. Coverage
is a separate focused 90% gate over the modernization boundary modules;
integration tests require explicit credentials or services and must never be
treated as offline tests.

Latest offline run: **1504 passed, 21 skipped** (`.env` present), and
**1501 passed, 21 skipped** with `.env` removed to mirror CI, which has no
`.env` file.

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
- The first turn of a brand-new conversation runs under the server default
  (`controlled`) because `POST /v1/chat` creates and sends in one call; the
  operator's chosen mode is persisted immediately afterwards. The deviation is
  toward the stricter side.
- `ARTPM_LOG_FILE` ships empty so the log file name derives from
  `ARTPM_PROCESS_ROLE` (`artpm-api.log` / `artpm-ui.log` / `artpm-voice.log`),
  keeping concurrent Windows writers off one handle.

## Remaining External Verification

- PostgreSQL RLS requires a disposable PostgreSQL instance and a non-owner app
  role. Without one, the RLS integration test must remain an explicit skip.
- Live MCP verification requires `ART_ENABLE_INTEGRATION=1` and a valid
  Skills Forge configuration.
- Provider latency and failover need a controlled external model environment;
  offline tests must continue using fakes.
