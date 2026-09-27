# ArtPM Agent

**通用智能体 · 有记忆的办公助手**，离线优先。它读得进你给的文件、能把事做完并交付
可核验的文件成果（DOCX / XLSX / PPTX / PDF）、记住你定过的口径，而不是以「对话」
为单位生成文本。游戏美术外包（报价测算、需求评估、排期派单、进度预警、质量控制、
交付与复盘）是内置的**预置空间与技能包**，用来检验底座，不划定产品边界。

产品定位、可靠性下限与冻结清单以
[`docs/product/PRD.md`](docs/product/PRD.md) §0 为准（2026-09-27 修订：由
「企业级业务助手 · 美术项目经理」改回通用智能体）；本文件的「应用场景」与
「核心能力」是摘要，不定义边界。

核心业务能力和本地文件能力不依赖 API Key。项目提供三种入口：

- **Streamlit 对话控制平面**：以对话为主入口，侧栏管理会话与工作区；支持「提问 / 执行 / 计划」模式，计划在输入区上方经过定稿与授权后推进，交付物以内联成果卡呈现。旧工作台实现保留为兼容层，不再作为主导航入口。
- **FastAPI REST 网关**：供内部系统集成，默认监听 `127.0.0.1:8765`。
- **CLI**：使用 `python main.py` 或安装后的 `artpm-agent` 命令。

## 应用场景

面向游戏美术外包团队的项目制作人与项目经理。**优先级：办公流任务 > 知识库问答**
——agent 的第一职责是把事做完并交付文件，知识库是支撑层。五个核心场景
（编号与 PRD §3 一致）：

| 场景 | 做什么 | 交付物 |
| --- | --- | --- |
| **S0 表格清洗** | 上传人天/产能表，理解结构、按维度裁剪、汇总 | 保留表头格式的 `.xlsx` |
| **S1 报价测算** | 甲方需求表 → 按资产类型拆工时 → 分层费率 + 管理费 + 税 + 改稿预算 | 可发送的《报价单.docx》，内嵌计算依据 |
| **S2 排期与催办** | 生成任务图 → 按日费率与在手工时派单 → 每日定时巡检 → 催办投递 | 派单方案、催办清单、企业微信通知 |
| **S3 验收复盘** | 按 `revision_count` 与实际工时对比估算 → 沉淀为知识规则 | 《复盘报告.docx》+ 下次报价自动引用 |
| **S4 办公流**（优先族） | 周报、会议纪要、台账更新、待办分派、通知拟稿 | `.docx` / `.xlsx` + 企业微信摘要 |

> **现状说明**：S4 办公流已完成首个可交付成员 `weekly_report`：它读取真实任务数据，
> 生成并核验 `.docx`，并将交付物挂到 Job；会议纪要、台账更新、待办分派和通知拟稿仍在后续
> 里程碑中。实现边界与验收记录见 PRD §4.5、§11。

S3 → S1 的回写回路是产品核心价值。工作区同时承载报价单、合同、需求文档与本地素材，
检索范围严格限定在当前 `(tenant, workspace)` 内。

无 LLM、无外部向量库、无远程服务时，上述业务技能与文件能力保持离线可用；
启用外部服务只增加语义理解与通用问答。

## 技术栈与职责

| 层 | 当前实现 | 主要路径 |
| --- | --- | --- |
| UI | Streamlit 1.59.x、CSS design tokens | `artpm_agent/app.py`、`artpm_agent/views/`、`artpm_agent/ui/` |
| API | FastAPI 0.135.x、Uvicorn 0.51.x | `artpm_agent/api/` |
| Agent 主链 | Harness `run_turn()`、typed events、审批边界 | `artpm_agent/harness/`、`artpm_agent/runtime/` |
| 路由与技能 | 关键词/语义路由、业务技能、MCP 适配 | `artpm_agent/routing/`、`artpm_agent/skills/` |
| Provider | OpenAI、Anthropic、DeepSeek、智谱及自定义网关 | `artpm_agent/providers/` |
| 业务与记忆 | SQLite/SQLAlchemy/Alembic、会话库、记忆库、知识库 | `artpm_agent/database/`、`artpm_agent/memory/`、`alembic/` |
| 检索 | 本地 FAISS，按需 Qdrant；字面检索兜底 | `artpm_agent/retrieval/`、`artpm_agent/memory/*vector*` |
| 可选基础设施 | Redis、OpenTelemetry、Sentry、MinerU、OCR、LiveKit、MCP | `pyproject.toml` optional extras、`artpm_agent/core/`、`artpm_agent/voice/` |
| 生产部署 | Docker Compose、Caddy、PostgreSQL/RLS、Qdrant、Redis | `docker-compose.yml`、`Caddyfile`、`deploy/` |

根目录的 `agent.py`、`app.py`、`config.py`、`main.py` 和 `health_check.py` 是兼容 shim；
新增业务逻辑应进入 `artpm_agent/`，不要在兼容层继续扩展旧实现。

## 结构树与数据边界

```text
pmagent/
├── artpm_agent/       唯一正式源码包
│   ├── api/            REST 网关、身份和请求模型
│   ├── harness/        回合编排、记忆注入、技能/模型 handler
│   ├── runtime/        AgentLoop、事件总线、工具、计划和子 Agent
│   ├── retrieval/      workspace-scoped RetrievalPlan/Hit 适配层
│   ├── memory/         Conversation/Session/Knowledge/Vector stores
│   ├── providers/      模型路由、故障转移、结构化输出和缓存
│   ├── skills/         业务技能及路由
│   ├── tenancy/security/ 租户上下文、权限和审批
│   ├── views/           Streamlit 页面宿主和按职责拆分的渲染模块
│   ├── ui/              design token、页面样式片段和通用 UI 组件
│   └── ui_style.py      旧样式注入兼容 facade
├── tests/              单元、契约、慢速 UI 和显式集成测试
├── benchmarks/         离线性能门禁
├── scripts/            启动、迁移、质量检查和维护脚本
├── alembic/            PostgreSQL/业务数据库迁移
├── deploy/             Compose、PostgreSQL 和可观测部署资源
├── docs/               当前架构、运维、指南和归档文档
├── doc/                项目级文档扩展目录
├── prototype/          产品原型与交互规格
├── project/frontend/   独立前端代码预留目录
├── project/backend/    独立后端代码预留目录
├── database/           数据库脚本与运维资产预留目录
├── utils/              项目级工具预留目录
├── data/               运行时数据库、附件和向量索引（不提交）
└── logs/               运行时日志（不提交）
```

数据边界保持明确：`ConversationStore` 只负责用户可见会话消息和工作区元数据；
`SessionStore/EventBus` 负责回合及工具事件；`MemoryManager` 只保留兼容记忆入口；
`WorkspaceKnowledgeStore` 负责工作区资源、版本和已接受规则。写入会在同一事务登记
`knowledge_index_outbox`；projector 使用租约、指数退避和死信状态维护 FAISS/Qdrant
派生索引，索引滞后时自动回退字面检索。保留期、导出、压缩、用户删除和租户注销由
`MemoryLifecycleService` 统一治理，详见 `docs/architecture/MEMORY_LIFECYCLE.md`。
Redis 只是缓存加速层。所有 API、缓存和向量查询都必须带可信 `tenant_id/workspace_id`。

## 运行方式

| 场景 | 安装方式 | 入口 | 默认依赖 |
| --- | --- | --- | --- |
| 核心 CLI | `python -m pip install -e .` | `artpm-agent` | SQLite、确定性检索、Harness Runtime |
| 离线 UI | `python -m pip install -e ".[ui,documents,vector-local]"` | Streamlit | UI + 按需文档与本地向量能力 |
| 本地 UI + API | `python -m pip install -e ".[ui,api,documents,vector-local,llm-openai]"` | `python start_with_checks.py` | 显式组合所需能力 |
| 开发与测试 | `python -m pip install -e ".[production,dev]"` | `scripts/` 下的质量门禁 | 运行 profiles + 质量工具 |
| 生产 Compose | `python -m pip install -e ".[production]"` | `docker compose up -d --build` | PostgreSQL/RLS、Qdrant、Redis、可观测组件 |

基础安装不包含 UI、文档解析、FAISS、模型 SDK 或 MCP。按场景组合 extras；`dev` 只包含
测试、lint、类型与安全工具，不再复制生产依赖。

聊天界面采用 EvoFlow 兼容的对话控制平面：240px 侧栏管理会话与工作区，中间是 760px
阅读宽度的对话流；输入区支持「提问 / 执行 / 计划」模式，计划确认条固定在输入区上方。
助手正文保持无框阅读面，用户消息使用独立浅灰气泡，交付物与任务结果以内联成果卡呈现。
`artpm_agent/ui_style.py` 保留 Streamlit 兼容注入契约；新的计划交互进入
`artpm_agent/views/chat_plan_mode.py`，页面渲染职责进入 `artpm_agent/views/`。

## 核心能力

下表状态列是 2026-09-19 的实测结论，不是路线图。「已验证」表示有端到端使用证据，
「已实现未验证」表示管道通了但没有真实使用记录。收缩计划见 PRD §5。

### 业务技能（离线可用，不依赖 API Key）

| 能力 | 说明 | 状态 |
| --- | --- | --- |
| 报价与成本 | 报价、成本、利润、管理费、税费和风险测算 | 已实现未验证：费率存在 4 处重复定义，公式缺改稿/账期/汇率（PRD R 系列） |
| 项目执行 | 任务分配、负载分析、进度追踪、截止日期预警 | 已实现未验证：`tasks`/`task_assignments` 表 0 行 |
| 交付流程 | 报价排期、需求评估、质量控制、交付、复盘 5 段技能 | 已实现未验证：5 段技能未被任何工作流串联，用户自建流程 0 条 |
| 文档处理 | Excel/PDF/TXT/CSV/JSON 读取与结构化抽取 | 部分缺陷：Excel 附件被硬编码分类为「报价单」（PRD R-3） |
| 文件交付 | 生成并核验 DOCX、XLSX、PPTX、PDF，通过后提供下载 | 已验证（简单请求）；复杂业务请求尚未接通（PRD R-4） |
| 本地分析 | 文件搜索、数据分析、趋势分析、项目健康度 | 已验证 |
| 通知投递 | 生成提醒，企业微信 Webhook 投递 | 已实现未验证：`reminders` 表 0 行 |

### 配置 LLM 后增强

- 通用自然语言对话和语义理解。
- Provider 故障转移（收缩后保留 1 主 1 备）、请求限时和响应缓存。
- DeepSeek `deepseek-chat` / `deepseek-reasoner`，支持独立捕获 `reasoning_content` 和
  `DEEPSEEK_REASONING_EFFORT`。
- 视觉模型搭配：主模型不支持图片时，将图片请求路由到独立视觉模型。
- 模型工具调用：参数先通过 JSON Schema 校验，写入型工具仍需宿主审批。

### 任务生成文件

安装 `documents` profile 后，可以在任务中提出文件交付要求，例如：

```text
生成一份 Excel 项目清单，列为任务、负责人、状态
制作一份 PDF 报价简报，内容为“验收已经完成”
把这张人天表裁剪成仅含 11 月的汇总版
```

明确内容的简单请求走本地确定性计划；复杂排版和多页内容先由模型生成严格 JSON 计划。
显式文件请求一旦命中就不会回落到普通聊天。系统会重新打开生成物，核对页数、幻灯片、
工作表或段落以及请求文本，并校验发布副本的大小和 SHA-256；只有 `verification.status=passed`
的文件才会进入下载卡片。当前架构契约见
[`docs/architecture/ARTIFACT_GENERATION.md`](docs/architecture/ARTIFACT_GENERATION.md)，
模板与字段规格见 [`docs/product/交付物模板库.md`](docs/product/交付物模板库.md)。

## 快速开始

### Windows PowerShell

```powershell
# 可选：创建并启用虚拟环境
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 本地 UI + API；模型、MCP、OCR 等能力继续按需追加 profile
python -m pip install -e ".[ui,api,documents,vector-local,llm-openai]"
Copy-Item .env.example .env

# 检查配置。无 LLM Key 也可以通过离线检查
python -m artpm_agent.tools.check_config

# 同时启动 FastAPI 和 Streamlit；启动器会等待 /ready
python start_with_checks.py
```

访问：

- UI：`http://127.0.0.1:8501`
- API 服务入口：`http://127.0.0.1:8765/`
- API 健康检查：`http://127.0.0.1:8765/health`
- API 就绪检查：`http://127.0.0.1:8765/ready`
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

也可以使用 `./start.sh`。脚本会启动相同的本地 UI + API 栈；Linux/macOS 脚本在检测到
FastAPI 缺失时会自动安装 `.[api]`，Windows `start.bat` 会提示使用同一安装命令。

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

Compose 会强制 `ARTPM_DEPLOYMENT_MODE=server`，生产 UI 不提供桌面文件选择器；数据目录只能
通过 `DATA_ROOT` 和宿主挂载管理。不要把该值改回 `local` 来绕过服务端边界。

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
- `POST /v1/chat/stream`（SSE 生命周期与快照）
- `GET /v1/workspaces`
- `POST /v1/workspaces`（需要 admin）
- `POST /v1/search`（工作区范围检索）
- 可选语音：`GET /v1/voice/status`、`POST /v1/voice/sessions`
- 权限审批：`/v1/permissions/*`
- 工作流及运行记录：`/v1/workflows/*`、`/v1/workflow-runs/*`

生产网关要求经过认证的租户、workspace、actor 上下文，并使用 `X-Gateway-Token`。完整的
请求体、身份头、错误码和审批契约见
[`docs/operations/API_GATEWAY_DOCUMENTATION.md`](docs/operations/API_GATEWAY_DOCUMENTATION.md) 与
[`docs/operations/API_GATEWAY.md`](docs/operations/API_GATEWAY.md)。

嵌入式网站能力默认关闭。启用时使用 `/embed/{channel}/config`、`exchange`、
`session` 和 `chat` 四个端点；发布令牌只在服务端交换，浏览器只拿短期来源绑定会话令牌。
配置、限流和 `frame-ancestors` 约束见
[`docs/guides/WORKSPACE_RETRIEVAL_API.md`](docs/guides/WORKSPACE_RETRIEVAL_API.md)。

最小配置示例：

```dotenv
ARTPM_EMBED_ENABLED=1
ARTPM_EMBED_SECRET=replace-with-a-long-random-secret
ARTPM_EMBED_CHANNEL=default
ARTPM_EMBED_PUBLISH_TOKEN=server-only-publish-token
ARTPM_EMBED_WORKSPACE_ID=local-default
ARTPM_EMBED_ALLOWED_ORIGINS=https://app.example.com
```

发布令牌只能由服务端调用 `exchange`，不能写入浏览器脚本。Origin 必须是精确的
`http(s)` origin；不要使用 `*`、凭据、路径或把长期密钥放入前端。

## 可选能力

### MinerU 多模态文档解析

支持 PDF、图片、DOCX、PPTX、XLSX 转 Markdown 和结构化 JSON。

```bash
# 推荐：远程 mineru-api 客户端，不下载本地 VLM/OCR 权重
python -m pip install -e ".[mineru-client]"
```

本地 pipeline（`.[mineru]`，`utils/mineru_adapter.py` 1,368 行）已列入 PRD §5 收缩
清单，只保留远程 API 客户端；在删除完成前不要依赖本地模式。

MinerU 是增强后端；未部署、转换失败或超时都会回退到内置解析器。配置细节见
[`docs/integrations/MINERU_INTEGRATION.md`](docs/integrations/MINERU_INTEGRATION.md)。

### OCR 与其他可选能力

| 能力 | 启用方式 | 状态 |
| --- | --- | --- |
| OCR | `python -m pip install -e ".[ocr]"` | **保留**——报价单与扫描件是 S1 入口，重依赖按需安装 |
| 实时语音（LiveKit） | — | **列入删除**（PRD §5，1,424 行） |
| 远程 MCP（Skills Forge） | — | **列入删除**（PRD §5，约 2,400 行），业务工具本地化 |
| Redis 缓存 | — | **不启用则删除适配层**（PRD §5） |
| Qdrant 远程向量 | — | **删除**，保留本地 FAISS |
| 插件系统 | — | **删除**（PRD §5，1,274 行） |
| Embed 网页嵌入 | — | **冻结**，不修不测 |

MCP、语音、插件在删除完成前仍可通过配置关闭，但**不要在新功能中依赖它们**，也不要
为它们补充文档或测试。完整清单与理由见
[`docs/product/PRD.md`](docs/product/PRD.md) §5「明确不做」。

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
- `RuntimeFactory` 与 `StorageRegistry` 是 API/UI/CLI 的进程级服务构造入口；Store、Agent、
  WorkflowCoordinator 和学习服务不得在每回合重新构造。
- `ArtPMAgent` 是兼容 facade，新功能不应继续扩大对旧 facade 私有实现的依赖。
- 未绑定权威知识库的 `MemoryManager.save_document()` 已退役；工作区知识统一写入
  `StorageRegistry.knowledge`。
- API chat 优先使用 async handler；同步旧 Agent 在迁移完成前通过线程隔离。
- MCP 的远程 HTTP、本地文件解析、数据分析和命令执行均在线程中隔离，
  不得阻塞 API/Agent 主事件循环。
- 业务库和记忆库分离，workspace/tenant 隔离必须贯穿查询、写入、缓存和向量检索。
- 模型工具调用先过 JSON Schema；写入型操作必须经过宿主审批。

## 开发与质量验证

安装开发依赖：

```bash
python -m pip install -e ".[production,dev]"
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

# 报价正确性：结构性 golden 断言（ST-1..ST-7），M1 起阻塞
python scripts/quote_golden_check.py

# 报价正确性：真值断言（V-1..V-3），需要 tests/golden/cases/ 的真实报价单
# cases/ 为空时退出码 3 = 未验证。CI 会因此变红，这是有意的：
# 在拿到业务真值前，「报价功能已验证」的结论不成立。
python scripts/quote_golden_check.py --values

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

- [`docs/product/PRD.md`](docs/product/PRD.md)：**定位权威**——场景、收缩清单、
  对象模型、界面规格、指标与里程碑。与本页冲突时以 PRD 为准。
- [`docs/product/领域数据契约.md`](docs/product/领域数据契约.md)：费率、资产类型、
  供应商分层与改稿系数的业务数据来源。
- [`docs/product/交付物模板库.md`](docs/product/交付物模板库.md)：报价单、复盘报告、
  催办通知的字段与版式规格。
- [`docs/roadmaps/NEXT_STEPS.md`](docs/roadmaps/NEXT_STEPS.md)：当前一周动作。
- [`docs/INDEX.md`](docs/INDEX.md)：完整文档索引。
- [`docs/operations/CURRENT_STATUS.md`](docs/operations/CURRENT_STATUS.md)：当前架构、质量门禁和外部验证状态。
- [`docs/operations/QUALITY_GATES.md`](docs/operations/QUALITY_GATES.md)：测试分层和质量命令。
- [`docs/operations/DEPENDENCY_PROFILES.md`](docs/operations/DEPENDENCY_PROFILES.md)：本地、API、开发和生产依赖。
- [`docs/architecture/CURRENT.md`](docs/architecture/CURRENT.md)：当前 Harness、工作区、检索和嵌入契约。
- [`docs/architecture/P2_MODULE_BOUNDARIES.md`](docs/architecture/P2_MODULE_BOUNDARIES.md)：大模块拆分后的纯职责模块与兼容 facade 边界。
- `LocalHarnessRuntime -> run_turn()`：API、Streamlit 和 CLI 的统一运行时主链；`/health` 返回进程级 `runtime_counters` 诊断快照。
- [`docs/architecture/STORAGE_CONTRACT.md`](docs/architecture/STORAGE_CONTRACT.md)：业务库、会话库、知识库、向量和缓存边界。
- [`docs/architecture/OPTIMIZATION_STRATEGY.md`](docs/architecture/OPTIMIZATION_STRATEGY.md)：大模块拆分、兼容层和风险治理策略。
- [`docs/architecture/PROJECT_STRUCTURE.md`](docs/architecture/PROJECT_STRUCTURE.md)：仓库目录、入口和数据目录说明。
- [`docs/architecture/EXECUTION_MAP.md`](docs/architecture/EXECUTION_MAP.md)：请求编排、上下文、记忆、工具和记录的执行映射。
- [`docs/guides/QUICK_REFERENCE.md`](docs/guides/QUICK_REFERENCE.md)：常用模块和命令速查。
- [`docs/architecture/ARCHITECTURE_DIAGRAM.md`](docs/architecture/ARCHITECTURE_DIAGRAM.md)：架构图谱。
- [`docs/guides/USER_GUIDE.md`](docs/guides/USER_GUIDE.md)：用户使用教程。
- [`docs/architecture/MULTI_TENANT_ARCHITECTURE.md`](docs/architecture/MULTI_TENANT_ARCHITECTURE.md)：多租户隔离设计。
- [`docs/dev/PLUGIN_DEVELOPMENT_GUIDE.md`](docs/dev/PLUGIN_DEVELOPMENT_GUIDE.md)：插件开发。
- [`docs/integrations/MCP.md`](docs/integrations/MCP.md)：MCP 可选集成、统一客户端和安全边界。
- [`docs/operations/PRODUCTION_MODERNIZATION.md`](docs/operations/PRODUCTION_MODERNIZATION.md)：生产现代化边界。
- [`docs/operations/TROUBLESHOOTING.md`](docs/operations/TROUBLESHOOTING.md)：故障排查。

`docs/archive/` 下的文档仅用于历史追溯，不作为当前架构或配置契约。

## 故障排查

1. 先运行 `python -m artpm_agent.tools.check_config`，确认配置结构和必填项。
2. API 启动失败时确认已安装 `.[api]`，并检查 `8765` 是否被其他程序占用。
3. UI 不能访问 API 时检查 `http://127.0.0.1:8765/health`；生产环境检查 Caddy、API 和
   `ARTPM_GATEWAY_SHARED_SECRET`。
4. Qdrant、Redis 或 MinerU 不可用时查看启动日志和回退原因；离线模式应保持可用。
5. PostgreSQL RLS 测试没有 `ARTPM_TEST_POSTGRES_URL` 时会明确 skip，不应报告为已验证。

## 许可证

MIT，见 `pyproject.toml`。
