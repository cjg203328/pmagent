# ArtPM Agent

面向游戏美术外包项目管理的 AI 助手，采用离线优先设计。它覆盖报价测算、任务分配、
进度预警、提醒投递、文档解析和本地文件分析；配置 LLM 后可启用通用对话、语义理解和
模型工具调用。

核心业务技能和本地文件能力不依赖 API Key。项目提供三种主要入口：

- **Streamlit UI**：主界面，提供对话、设置、可观测和工作流功能。
- **FastAPI REST 网关**：供外部系统集成，默认监听 `127.0.0.1:8765`。
- **CLI**：使用 `python main.py` 或安装后的 `artpm-agent` 命令。

## 运行方式

| 场景 | 安装方式 | 入口 | 默认依赖 |
| --- | --- | --- | --- |
| 离线 UI / CLI | `python -m pip install -e .` | Streamlit 或 CLI | SQLite、FAISS |
| 本地 UI + API | `python -m pip install -e ".[api]"` | `python start_with_checks.py` | 上述依赖 + FastAPI、Uvicorn |
| 开发与测试 | `python -m pip install -e ".[dev]"` | `scripts/` 下的质量门禁 | 本地依赖 + 测试和质量工具 |
| 生产 Compose | `python -m pip install -e ".[production]"` | `docker compose up -d --build` | PostgreSQL/RLS、Qdrant、Redis、可观测组件 |

基础安装不包含 FastAPI。需要启动完整本地栈时，请安装 `.[api]` 或 `.[dev]`；只运行离线
UI 时直接使用基础安装即可。

## 核心能力

### 离线可用

| 能力 | 说明 |
| --- | --- |
| 报价与成本 | 报价、成本、利润、管理费、税费和风险测算 |
| 项目执行 | 任务分配、负载分析、进度追踪和截止日期预警 |
| 交付流程 | 报价排期、需求评估、质量控制、交付和复盘 |
| 文档处理 | 文档分类、结构化抽取、Excel/PDF/TXT/CSV/JSON 读取 |
| 本地分析 | 文件搜索、数据分析、趋势分析和项目健康度评估 |
| 通知投递 | 生成提醒，可选企业微信 Webhook 投递 |

### 配置 LLM 后增强

- 通用自然语言对话和语义理解。
- 多 Provider 故障转移、请求限时和响应缓存。
- DeepSeek `deepseek-chat` / `deepseek-reasoner`，支持独立捕获 `reasoning_content` 和
  `DEEPSEEK_REASONING_EFFORT`。
- 视觉模型搭配：主模型不支持图片时，将图片请求路由到独立视觉模型。
- 模型工具调用：参数先通过 JSON Schema 校验，写入型工具仍需宿主审批。

## 快速开始

### Windows PowerShell

```powershell
# 可选：创建并启用虚拟环境
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 本地完整栈需要 [api]；只使用离线 UI 时改为 python -m pip install -e .
python -m pip install -e ".[api]"
Copy-Item .env.example .env

# 检查配置。无 LLM Key 也可以通过离线检查
python -m artpm_agent.tools.check_config

# 同时启动 FastAPI 和 Streamlit
python start_with_checks.py
```

访问：

- UI：`http://127.0.0.1:8501`
- API 健康检查：`http://127.0.0.1:8765/health`
- API 文档：`http://127.0.0.1:8765/docs`

只启动离线 UI：

```powershell
python -m pip install -e .
python -m streamlit run artpm_agent/app.py --server.address 127.0.0.1 --server.port 8501
```

也可以在依赖安装完成后使用 `start.bat`。它会调用统一启动器；配置检查失败或端口被其他
程序占用时会停止启动。仅检查配置而不启动服务：

```powershell
python start_with_checks.py --check-only
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[api]"
cp .env.example .env
python -m artpm_agent.tools.check_config
python start_with_checks.py
```

也可以使用 `./start.sh`。脚本会启动相同的本地 UI + API 栈。

## 配置

从 `.env.example` 创建 `.env`，只填写当前部署需要的配置。无 LLM Key 时，系统保持离线
模式，不应发起无效的模型请求。

常用配置：

```dotenv
# LLM_PROVIDER 可选 anthropic / openai / zhipu / deepseek / custom
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=

# 默认关闭。只有接入宿主审批钩子后才启用
AGENT_MODEL_TOOL_CALLS_ENABLED=false

# 本地数据默认落在 data/
DB_PATH=./data/artpm.db
MEMORY_DB_PATH=./data/memory.db
VECTOR_DB_PATH=./data/vector_store
```

关键行为：

- `ARTPM_DEPLOYMENT_MODE=local` 用于本机模式，生产 Compose 使用 `server`/生产环境配置。
- `VECTOR_BACKEND=qdrant` 只有在 `QDRANT_URL` 非空且 Qdrant 可用时使用远程后端；否则使用
  本地 FAISS，运行期远程故障也会回退到 FAISS。
- `DATABASE_URL` 配置 PostgreSQL 时使用数据库隔离；本地默认使用 SQLite。SQLite 不提供
  数据库级 RLS，应用层仍必须保持 tenant/workspace 隔离。
- `REDIS_URL` 只启用缓存加速层，SQLite 仍是权威数据源；缺失或不可达时自动降级。
- `ARTPM_TELEMETRY=0` 可关闭遥测写入；`OTEL_EXPORTER_OTLP_ENDPOINT` 和 `SENTRY_DSN`
  未配置时对应集成为 no-op。
- 生产环境不要把 `AGENT_MODEL_TOOL_CALLS_ENABLED` 当作审批替代品，也不要关闭 TLS 校验。

## Docker 生产部署

Compose 对外只暴露 Caddy 的 `80/443`；Streamlit `8501` 和 REST API `8765` 只在容器网络内
供 Caddy 访问。Grafana 仅绑定宿主机 `127.0.0.1:3000`。完整部署说明见
[`docs/operations/DEPLOY.md`](docs/operations/DEPLOY.md)。

首次部署前，在 `.env` 中设置以下必填值：

- `BASIC_AUTH_USER`、`BASIC_AUTH_HASH`：Caddy 基础认证凭据。
- `ARTPM_GATEWAY_SHARED_SECRET`：API 网关共享密钥。
- `POSTGRES_PASSWORD`：应用角色密码。
- `POSTGRES_ADMIN_PASSWORD`：迁移角色密码。
- `GRAFANA_ADMIN_PASSWORD`：Grafana 管理员密码。

生成 Caddy bcrypt 密码哈希并启动：

```bash
cp .env.example .env
docker run --rm caddy:2-alpine caddy hash-password
# 将输出填入 .env 的 BASIC_AUTH_HASH，并设置 BASIC_AUTH_USER
docker compose config
docker compose up -d --build
docker compose ps
```

公网部署时设置已解析到服务器的 `SITE_ADDRESS`。留空时使用 `localhost` 和 Caddy 本地 CA，
浏览器可能需要先信任该证书。`./data`、`./logs` 和具名卷会持久化业务数据、记忆、向量索引、
缓存、数据库、证书及可观测数据；生产 Compose 禁用 SQLite fallback，PostgreSQL/RLS 失败
不会静默降级到 SQLite。

## REST API

安装 API extra 后可使用以下入口：

```powershell
python -m pip install -e ".[api]"
artpm-api
# 或
python -m artpm_agent.api
```

主要路由：

- `GET /health`、`GET /ready`
- `GET /v1/capabilities`
- `POST /v1/chat`
- 权限审批：`/v1/permissions/*`
- 工作流及运行记录：`/v1/workflows/*`、`/v1/workflow-runs/*`

生产网关要求经过认证的租户、workspace、actor 上下文，并使用 `X-Gateway-Token`。完整的
请求体、身份头、错误码和审批契约见
[`docs/operations/API_GATEWAY_DOCUMENTATION.md`](docs/operations/API_GATEWAY_DOCUMENTATION.md) 与
[`docs/operations/API_GATEWAY.md`](docs/operations/API_GATEWAY.md)。

## 可选能力

### MinerU 多模态文档解析

支持 PDF、图片、DOCX、PPTX、XLSX 转 Markdown 和结构化 JSON。按部署方式选择：

```bash
# 本地 pipeline，包含模型运行时
python -m pip install -e ".[mineru]"

# 远程 mineru-api 客户端，不下载本地 VLM/OCR 权重
python -m pip install -e ".[mineru-client]"
```

MinerU 是增强后端；未部署、转换失败或超时都会回退到内置解析器。配置细节见
[`docs/integrations/MINERU_INTEGRATION.md`](docs/integrations/MINERU_INTEGRATION.md)。

### OCR、语音、MCP 与 Redis

| 能力 | 启用方式 | 说明 |
| --- | --- | --- |
| OCR | `python -m pip install -e ".[ocr]"` | 适用于报价单和扫描件，重依赖，按需安装 |
| 实时语音 | `python -m pip install -e ".[voice]"` | 配置 LiveKit 与 STT/TTS 后运行 `artpm-voice-worker start`，文本通道不依赖它 |
| Skills Forge MCP | `MCP_ENABLED=true` + 有效 `SKILLS_FORGE_KEY` | 默认只读发现工具，命令执行仍需显式 `MCP_ALLOW_COMMANDS=true` |
| Redis | 设置 `REDIS_URL` | 可选缓存加速层，不是 SQLite 权威源的替代品 |

MCP 默认关闭。普通业务和本地文件工具不依赖远程 MCP；真实 MCP 验证必须显式设置集成开关。
语音转录仍进入既有会话、记忆、检索和审批链路，不绕过文本通道的安全边界。

## 架构边界

```text
artpm_agent/
├── app.py / views/       Streamlit 入口与页面
├── api/                  FastAPI 网关
├── harness/              会话编排：模型、技能、知识、记忆和 Token 预算
├── runtime/              AgentLoop、事件总线、工具流水线、plan、subagent、会话查询
├── skills/               业务技能、本地文件技能和技能路由
├── providers/            Provider 故障转移、结构化输出和响应缓存
├── memory/               SQLite 会话、FAISS/Qdrant 向量和 workspace 知识库
├── database/             SQLAlchemy 模型、租户会话和 Alembic 迁移
├── security/ / tenancy/  审批、权限和租户上下文
└── plugins/              带 allowlist 和 SHA-256 校验的插件注册
```

当前请求边界：

- `HarnessRuntime` 与 `run_turn()` 是 API/UI 请求处理的规范入口。
- `ArtPMAgent` 是兼容 facade，新功能不应继续扩大对旧 facade 私有实现的依赖。
- API chat 优先使用 async handler；同步旧 Agent 在迁移完成前通过线程隔离。
- 业务库和记忆库分离，workspace/tenant 隔离必须贯穿查询、写入、缓存和向量检索。
- 模型工具调用先过 JSON Schema；写入型操作必须经过宿主审批。

## 开发与质量验证

安装开发依赖：

```bash
python -m pip install -e ".[dev]"
```

按变更范围执行门禁：

```powershell
# 快速离线回归
powershell -ExecutionPolicy Bypass -File scripts/test_fast.ps1

# 完整离线回归，包含慢速 UI 测试
powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1

# 显式外部集成：MCP、网络、凭据或 PostgreSQL
powershell -ExecutionPolicy Bypass -File scripts/test_integration.ps1

# 离线性能门禁
powershell -ExecutionPolicy Bypass -File scripts/test_benchmark.ps1

# 现代化边界核心覆盖率，门槛为 90%
powershell -ExecutionPolicy Bypass -File scripts/coverage_core.ps1

# 静态与语法检查
ruff check artpm_agent tests
python -m compileall -q artpm_agent
```

Linux/macOS 的快速回归和覆盖率命令：

```bash
python -m pytest -q --no-cov -m "not integration and not benchmark and not slow"
python scripts/coverage_core.py
```

集成测试必须显式 opt-in，不能把 mock、skip 或静态检查描述为真实生产验证。`mypy artpm_agent`
用于渐进式类型检查，当前仍可能包含既有基线错误；修改类型边界时应区分新增错误和既有错误。

## 文档索引

- [`docs/INDEX.md`](docs/INDEX.md)：完整文档索引。
- [`docs/operations/CURRENT_STATUS.md`](docs/operations/CURRENT_STATUS.md)：当前架构、质量门禁和外部验证状态。
- [`docs/operations/QUALITY_GATES.md`](docs/operations/QUALITY_GATES.md)：测试分层和质量命令。
- [`docs/operations/DEPENDENCY_PROFILES.md`](docs/operations/DEPENDENCY_PROFILES.md)：本地、API、开发和生产依赖。
- [`docs/architecture/PROJECT_STRUCTURE.md`](docs/architecture/PROJECT_STRUCTURE.md)：仓库目录、入口和数据目录说明。
- [`docs/architecture/EXECUTION_MAP.md`](docs/architecture/EXECUTION_MAP.md)：请求编排、上下文、记忆、工具和记录的执行映射。
- [`docs/guides/QUICK_REFERENCE.md`](docs/guides/QUICK_REFERENCE.md)：常用模块和命令速查。
- [`docs/architecture/ARCHITECTURE_DIAGRAM.md`](docs/architecture/ARCHITECTURE_DIAGRAM.md)：架构图谱。
- [`docs/guides/USER_GUIDE.md`](docs/guides/USER_GUIDE.md)：用户使用教程。
- [`docs/architecture/MULTI_TENANT_ARCHITECTURE.md`](docs/architecture/MULTI_TENANT_ARCHITECTURE.md)：多租户隔离设计。
- [`docs/dev/PLUGIN_DEVELOPMENT_GUIDE.md`](docs/dev/PLUGIN_DEVELOPMENT_GUIDE.md)：插件开发。
- [`docs/integrations/LOCAL_MCP_GUIDE.md`](docs/integrations/LOCAL_MCP_GUIDE.md)：本地 MCP 工具。
- [`docs/operations/PRODUCTION_MODERNIZATION.md`](docs/operations/PRODUCTION_MODERNIZATION.md)：生产现代化边界。
- [`docs/operations/TROUBLESHOOTING.md`](docs/operations/TROUBLESHOOTING.md)：故障排查。

## 故障排查

1. 先运行 `python -m artpm_agent.tools.check_config`，确认配置结构和必填项。
2. API 启动失败时确认已安装 `.[api]`，并检查 `8765` 是否被其他程序占用。
3. UI 不能访问 API 时检查 `http://127.0.0.1:8765/health`；生产环境检查 Caddy、API 和
   `ARTPM_GATEWAY_SHARED_SECRET`。
4. Qdrant、Redis 或 MinerU 不可用时查看启动日志和回退原因；离线模式应保持可用。
5. PostgreSQL RLS 测试没有 `ARTPM_TEST_POSTGRES_URL` 时会明确 skip，不应报告为已验证。

## 许可证

MIT，见 `pyproject.toml`。
