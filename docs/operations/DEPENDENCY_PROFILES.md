# Dependency Profiles

Status: current（本页描述**当前可安装状态**）+ target（「删除」标注项为**目标状态**）
Validated on 2026-09-19 from branch `chore/consolidate-uncommitted-work`.

The base package is the core Runtime/CLI surface. UI frameworks, document
parsers, vector engines, model SDKs and external integrations are installed as
capabilities so a minimal worker does not carry several hundred MiB of unused
packages.

## 当前状态 vs 目标状态

| 项 | 当前状态（2026-09-19 实测） | 目标状态 |
| --- | --- | --- |
| 可安装 profile 数量 | 表中全部 profile 均可安装 | 删除项移除后收敛 |
| 标注「删除」的 profile | 仍在 `pyproject.toml` 中，可安装、有测试引用 | 随收缩迁移手册 S-5 逐项移除 |
| `production` composite | 打包语音 / MCP / Qdrant / Redis，与 PRD §5 冲突 | 重写为只含保留能力 |
| `documents` | 已安装，S0/S1 主链在用 | 保持不变 |

标注「删除」的 profile 在移除完成前仍是**当前可用**状态，本页不会把它们写成已删除。

## Profile Matrix

| Profile | Purpose | Main packages | 状态 |
| --- | --- | --- | --- |
| base / `runtime` | Harness, SQLite, configuration and CLI | SQLAlchemy, Alembic, Pydantic, NumPy | 保留 |
| `api` | REST gateway | FastAPI, Uvicorn | 保留 |
| `ui` | Streamlit application | Streamlit, pandas, Plotly | 保留（改版见 PRD §7） |
| `documents` | Office/PDF parsing and generation | openpyxl, python-docx, python-pptx, reportlab, pdfplumber, PyMuPDF | **保留——S0/S1 核心** |
| `vector-local` | Local derived index | FAISS CPU | 保留 |
| `vector-remote` | Remote derived index | Qdrant client | **删除**（PRD §5） |
| `llm-openai` / `llm-anthropic` | Native provider SDK | OpenAI or Anthropic SDK | 保留（收敛为 1 主 1 备） |
| `llm-langchain` | Optional LangChain bridge | LangChain integrations and LangGraph | **删除候选**——未被业务技能依赖 |
| `mcp` | Model Context Protocol | MCP SDK | **删除**（PRD §5） |
| `ocr` | Optional document backend | PaddleOCR | **保留——报价单/扫描件入口** |
| `mineru` | Local MinerU pipeline | MinerU pipeline | **删除**，只留 `mineru-client` |
| `mineru-client` | Remote MinerU API | MinerU client | 保留 |
| `voice` | Optional voice worker | LiveKit providers and local TTS | **删除**（PRD §5） |
| `observability` | Exported tracing/errors | OpenTelemetry and Sentry | 冻结，不扩展 |
| `postgres` | Production state adapter | psycopg | 保留 |
| `cache` | Cache adapter | Redis | **删除候选**（PRD §5） |
| `dev` | Quality gates only | pytest, Ruff, mypy, Bandit, Safety | 保留 |
| `production` | Deployable composite | UI/API/documents/vector/model/MCP plus remote adapters | **需随收缩重写**——当前包含全部待删能力 |

标注为删除的 profile 在移除完成前仍可安装，但不要在新文档、新测试或新部署示例中
引用它们。`production` 这一组是当前最大的不一致来源：它把语音、MCP、Qdrant、Redis
全部打包，与 PRD §5 直接冲突。

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
