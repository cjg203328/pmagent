# Dependency Profiles

The base package is the core Runtime/CLI surface. UI frameworks, document
parsers, vector engines, model SDKs and external integrations are installed as
capabilities so a minimal worker does not carry several hundred MiB of unused
packages.

## Profile Matrix

| Profile | Purpose | Main packages |
| --- | --- | --- |
| base / `runtime` | Harness, SQLite, configuration and CLI | SQLAlchemy, Alembic, Pydantic, NumPy |
| `api` | REST gateway | FastAPI, Uvicorn |
| `ui` | Streamlit application | Streamlit, pandas, Plotly |
| `documents` | Office/PDF parsing and generation | openpyxl, python-docx, python-pptx, reportlab, pdfplumber, PyMuPDF |
| `vector-local` | Local derived index | FAISS CPU |
| `vector-remote` | Remote derived index | Qdrant client |
| `llm-openai` / `llm-anthropic` | Native provider SDK | OpenAI or Anthropic SDK |
| `llm-langchain` | Optional LangChain bridge | LangChain integrations and LangGraph |
| `mcp` | Model Context Protocol | MCP SDK |
| `ocr` / `mineru` / `mineru-client` | Optional document backends | PaddleOCR or MinerU |
| `voice` | Optional voice worker | LiveKit providers and local TTS |
| `observability` | Exported tracing/errors | OpenTelemetry and Sentry |
| `postgres` / `cache` | Production state adapters | psycopg or Redis |
| `dev` | Quality gates only | pytest, Ruff, mypy, Bandit, Safety |
| `production` | Deployable composite | UI/API/documents/vector/model/MCP plus remote adapters |

`runtime` is an empty compatibility marker because the base dependency set is
the runtime profile. PEP 621 extras cannot portably include other extras, so
deployment commands explicitly compose profiles; only `production` is kept as
a convenience composite for wheel/Docker installation. `dev` intentionally
does not copy production dependencies.

```powershell
# Core CLI/runtime
python -m pip install -e .

# Offline UI
python -m pip install -e ".[ui,documents,vector-local]"

# Local UI/API with native OpenAI-compatible models
python -m pip install -e ".[ui,api,documents,vector-local,llm-openai]"

# Full regression environment
python -m pip install -e ".[production,dev]"
```

Optional code must stay behind lazy import boundaries. Missing optional
packages must produce a capability-unavailable status, not prevent the core
Runtime from importing. FAISS/Qdrant remain rebuildable indexes; Redis remains
a cache and neither is an authoritative Store.

## Compatibility

- `vector` remains a deprecated alias for `vector-local` for one compatibility
  cycle. New documentation must use `vector-local`.
- `requirements*.txt` are installer shims; `pyproject.toml` is authoritative.
- Archived documents describe historical states and are not dependency
  contracts. This document and `pyproject.toml` are the current contract.

Every profile change must pass dependency-profile tests, a fresh core import
check and `uv lock --check`.
