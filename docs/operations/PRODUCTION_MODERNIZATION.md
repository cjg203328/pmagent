# Production modernization

This release implements the P0-P3 modernization track while preserving local
operation.

## Architecture

- `artpm_agent.agent.ArtPMAgent` is a stable facade under 300 lines.
- `ComponentFactory` constructs dependencies and returns an immutable
  `ComponentRegistry`.
- `RequestOrchestrator` owns request behavior and compatibility methods.
- Skills expose `execute_async()`; synchronous implementations are isolated
  with `asyncio.to_thread()`.

## Cache v3

Response cache keys use the `response-v3` namespace. Image identity is derived
from SHA-256 content, never a temporary path. Unreadable image content makes a
request uncacheable. Tenant and workspace remain part of every key.

## PostgreSQL and RLS

Set `DATABASE_URL=postgresql+psycopg://...` in production. Alembic revision
`c2d3e4f50617` adds `tenant_id` and `workspace_id` to business tables, enables
and forces PostgreSQL row-level security, and applies `USING` plus `WITH CHECK`
policies. SQLAlchemy sessions set trusted `app.tenant_id` and
`app.workspace_id` transaction variables and bind new rows to that context.

SQLite remains the explicit local fallback when `DATABASE_URL` is empty or the
PostgreSQL driver is unavailable and `ARTPM_SQLITE_FALLBACK=true`. SQLite does
not provide database-level RLS; it relies on the existing application scope
checks.

Run the live RLS test against a disposable database:

```powershell
$env:ART_ENABLE_INTEGRATION="1"
$env:ARTPM_TEST_POSTGRES_ADMIN_URL="postgresql+psycopg://migration-owner/..."
$env:ARTPM_TEST_POSTGRES_URL="postgresql+psycopg://non-owner-app/..."
python -m pytest tests/integration/test_postgresql_rls.py -q --no-cov
```

## Vector storage

`VECTOR_BACKEND=qdrant` and `QDRANT_URL` select Qdrant. Startup connection
failure and runtime request failure automatically switch to the existing local
FAISS store. Leave `QDRANT_URL` empty for offline/local FAISS mode.

## Observability

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to enable OpenTelemetry traces and metrics.
Set `SENTRY_DSN` to enable exception aggregation. Both integrations are no-op
when unconfigured or unavailable.

The Compose stack includes PostgreSQL, Qdrant, an OpenTelemetry Collector,
Tempo, Prometheus, and provisioned Grafana. Grafana is available on port 3000;
`GRAFANA_ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, and
`POSTGRES_ADMIN_PASSWORD` are required at Compose configuration time. The application
connects as the non-superuser `artpm_app`; do not replace it with the PostgreSQL
admin role because superusers bypass RLS. A one-shot `migrate` service owns DDL
and grants only table/sequence DML to `artpm_app`, so the application connection
cannot disable table policies. The Compose deployment disables the SQLite
fallback so a PostgreSQL/RLS failure cannot silently weaken tenant isolation.

## Verification

```powershell
python -m pytest -q
powershell -ExecutionPolicy Bypass -File scripts/coverage_core.ps1
```

Install production-only remote integrations with:

```powershell
python -m pip install -e ".[production]"
```

The default functional suite does not collect coverage. The production
boundary modules introduced by this modernization use the focused 90% gate in
`scripts/coverage_core.py`, which keeps CI feedback bounded and repeatable.
