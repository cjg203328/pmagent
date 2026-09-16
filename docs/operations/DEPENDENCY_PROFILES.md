# Dependency Profiles

The base package is the offline-first application surface. Remote services are
installed by role so a local skill-only deployment does not need PostgreSQL,
Qdrant, Redis, FastAPI, or telemetry SDKs.

## Profile Matrix

| Profile | Intended use | Adds | Lifecycle |
| --- | --- | --- | --- |
| core (`.`) | Local Streamlit, CLI, SQLite and FAISS | Application runtime and offline document/LLM adapters | Current default |
| `api` | Local REST gateway | FastAPI and Uvicorn | Current |
| `production` | Compose server runtime | API, PostgreSQL, Qdrant, Redis, OpenTelemetry and Sentry | Current production contract |
| `dev` | Tests, linting, typing and security checks | Quality tools plus the production integration surface | Current CI contract |
| `postgres` / `vector-remote` / `cache` / `observability` | Install one remote integration | psycopg, Qdrant, Redis, or telemetry SDKs | Current composable profiles |
| `mineru` / `mineru-client` | Local MinerU pipeline or remote MinerU API client | Pinned MinerU runtime or lightweight client | Current opt-in |
| `ocr` | PaddleOCR compatibility path | PaddleOCR and PaddlePaddle | Current opt-in, heavyweight |
| `voice` | LiveKit voice worker | LiveKit provider series and local TTS | Current opt-in |
| `vector` | Historical FAISS install spelling | FAISS (already in core) | Compatibility extra; deprecate before next major |

`production` and `dev` intentionally repeat the integration dependencies in
`pyproject.toml` because PEP 621 does not provide a portable way to compose
optional extras. Keep those lists equivalent when an integration is added or
removed, then refresh `uv.lock`. CI and `tests/test_dependency_profiles.py`
are the contract for this duplication.

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

## Deprecation And Retirement

- `requirements.txt`, `requirements-dev.txt`, and `requirements-security.txt`
  are compatibility install shims. Migrate CI and deployment documentation to
  `pyproject.toml` extras before removing them.
- `vector` is retained only for existing installers because FAISS is already
  a core dependency. Do not add new documentation using it; remove it in the
  next major release after checking downstream install telemetry.
- Do not remove `postgres`, `vector-remote`, `cache`, `observability`, `ocr`,
  `mineru*`, or `voice` solely because they are optional. Each has a lazy
  import boundary, configuration contract, or deployment use case.
- To retire any profile: mark it deprecated for one release, update the
  README, this matrix, CI, `uv.lock`, and profile tests, then remove it only in
  a major release with a migration note.

Every profile change must pass `uv lock --check`, the dependency profile tests,
and an import check in a clean environment where the profile is absent. The
core profile must continue to import and start without any remote-service
extra installed.
