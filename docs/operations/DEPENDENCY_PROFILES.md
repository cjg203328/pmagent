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

# Optional local neural embeddings (still offline)
python -m pip install -e ".[embedding-onnx]"
```

The application keeps these integrations optional at import time. An unset
Redis URL remains a SQLite-only cache mode; an unavailable Qdrant backend falls
back to FAISS; telemetry and Sentry remain no-op when their endpoints are not
configured. Production deployment must use the `production` profile because
Compose intentionally disables SQLite fallback and requires PostgreSQL/RLS.

## Embedding and vector settings

These settings live in the operator-managed `memory` config section and are
mapped from the environment by `artpm_agent/config.py`. They are deliberately
not exposed in the end-user settings page: index compatibility is a deployment
concern, not a per-conversation preference.

| Variable | Default | Effect |
| --- | --- | --- |
| `EMBEDDING_PROVIDER` | `local_feature_hash` | Selects the embedding provider (see below) |
| `EMBEDDING_DIMENSION` | `1536` | Vector width. Changing it rebuilds the index |
| `EMBEDDING_MODEL_PATH` | *(unset)* | Local `.onnx` weights; required by `onnx` |
| `EMBEDDING_MODEL_NAME` | *(unset)* | Label used in the index fingerprint |
| `VECTOR_SHARD_BY_SCOPE` | `0` | One FAISS index per tenant/workspace |

### Provider selection

| Provider | Extra needed | Network | Notes |
| --- | --- | --- | --- |
| `local_feature_hash` | none | no | Default. Character n-gram hashing. Purely **lexical**: "成本" and "开销" share no characters and score zero |
| `onnx` | `.[embedding-onnx]` | no | Local neural model via onnxruntime. Understands synonyms. Requires `EMBEDDING_MODEL_PATH` and a tokenizer |
| `remote` | none | yes | Embedding API through the configured LLM client; the client must expose `embed_texts()` or `embed()` |

A requested `onnx` or `remote` provider that cannot be constructed **degrades to
`local_feature_hash`** and records the reason on `fallback_reason` instead of
failing the turn. An unrecognised provider name raises, so a typo is not silently
treated as the offline default.

Every provider declares its own `fingerprint`, and the FAISS manifest records it.
Vectors from different providers occupy different spaces, so the manifest refuses
to query an index built by a different provider rather than returning confident
nonsense. **Switching provider or dimension therefore requires a rebuild**; the
existing recovery path rebuilds from the authoritative SQLite state.

### Per-scope vector sharding

`VECTOR_SHARD_BY_SCOPE=1` stores one FAISS index per `<tenant>/<workspace>` under
`<VECTOR_DB_PATH>/scopes/`. A query carrying tenant/workspace filters then reads
only that scope's directory instead of scanning every tenant's vectors.

It is off by default because it changes the on-disk layout. Enabling it against
an existing flat index sets `needs_rebuild` — the store refuses to read the old
layout as if it were sharded, which would silently hide all existing vectors.
Turning the switch back off restores the flat layout with no data migration.

Qdrant needs none of this: it isolates tenants through server-side payload
filters, so sharding only applies to the local FAISS backend.
