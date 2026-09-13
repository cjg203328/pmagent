# Dependency Profiles

The base package is the offline-first application surface. Remote services are
installed by role so a local skill-only deployment does not need PostgreSQL,
Qdrant, Redis, FastAPI, or telemetry SDKs.

```powershell
# Local Streamlit and offline skills
python -m pip install -e .

# REST gateway on a local machine
python -m pip install -e ".[api]"

# Production Compose/runtime dependencies
python -m pip install -e ".[production]"

# Development and test environment
python -m pip install -e ".[dev]"
```

The application keeps these integrations optional at import time. An unset
Redis URL remains a SQLite-only cache mode; an unavailable Qdrant backend falls
back to FAISS; telemetry and Sentry remain no-op when their endpoints are not
configured. Production deployment must use the `production` profile because
Compose intentionally disables SQLite fallback and requires PostgreSQL/RLS.
