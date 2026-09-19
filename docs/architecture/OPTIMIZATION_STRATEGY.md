# ArtPM Agent 风险优化策略

Status: current
Owner: pmagent maintainers
Review cadence: each release and every architecture-affecting change

这份文档是当前风险治理计划。`docs/archive/` 下的报告只用于追溯，不作为实现契约。

## 本轮执行状态（2026-09-18）

| 阶段 | 状态 | 可验证产物 |
| --- | --- | --- |
| P0 多工作区隔离 | 已完成 | `(tenant, workspace, profile)` scoped runtime、UI/API 回归 |
| P1 稳定性边界 | 已完成 | `/ready` authority probes、有界锁/模型 client/tool loop/spill 测试 |
| P2 治理基础 | 已完成 | API router、workflow schema/codec、Protocol ports、四包 strict gate |
| P2 后续迁移 | 进行中 | `api/app.py` 剩余 workspace/chat/embed/voice 路由、旧 UI facade 逐步迁移 |

本表中的“已完成”只代表本地离线契约已通过；PostgreSQL RLS、真实 MCP 和外部模型
延迟仍需显式集成环境验证。

## 目标架构

```text
UI / REST / Embed
       |
       v
trusted TenantContext + WorkspaceContext
       |
       v
RetrievalPlan -> bounded WorkspaceRetriever -> RetrievalHit/citation
       |
       v
Harness run_turn / AgentEvent stream / TurnResult
       |
       v
ConversationStore + SessionStore + approval boundary
```

统一原则：业务事实进入业务库，用户可见对话进入 `ConversationStore`，回合/工具/审批事件进入 `SessionStore`，知识事实进入 `WorkspaceKnowledgeStore`，向量索引和缓存只能作为可重建加速层。

## 1. 大模块拆分

### `ui_helpers.py`

按依赖方向拆成四层，保持旧导入只做 re-export：

1. `ui/workspace_selector.py`：工作区列表、切换和 scope 重置。
2. `ui/conversation_sidebar.py`：会话列表、创建、删除和搜索。
3. `ui/approvals.py`：权限、知识入库和工作流审批。
4. `ui/rendering.py`：消息、附件、引用抽屉和错误状态。

迁移规则：先提取无副作用函数，再迁移单个渲染块；每次只移动一个边界并保留回归测试。禁止把 API/SQLite 查询继续直接写进页面渲染函数。

### `ui_style.py`

将内联 CSS 按 `tokens`、`layout`、`chat`、`sidebar`、`states` 分组。组件只引用变量，不新增页面级硬编码颜色。每组拆分后由一个 `STYLE_CSS` 兼容出口拼接，避免 Streamlit 缓存失效。

### `workspace_knowledge_store.py`

按 authority boundary 拆成：

- `schema.py`：表结构、迁移和约束；
- `resources.py`：资源与版本；
- `rules.py`：提议、批准和规则；
- `vector_index.py`：可重建向量索引；
- `workspace_knowledge_store.py`：事务门面与兼容入口。

检索客户端只依赖 `artpm_agent/retrieval/contracts.py`，不得依赖内部 SQLite 行结构。

### `views/chat.py`

页面只保留 Streamlit 生命周期和渲染。回合执行、附件快照、检索和事件订阅全部走 Harness/API adapter。`stream_chat()` 只作为兼容输出，不得再次触发意图、Skill 或附件解析。

## 2. Harness 主链迁移

`run_turn()`、`TurnContext`、`TurnResult` 和 `AgentEvent` 是唯一新增业务逻辑入口。`ArtPMAgent`、`RequestOrchestrator` 和旧 facade 进入兼容层维护期：

- 新功能只能先添加 Harness handler 和 typed event；
- 兼容 facade 只能适配输入/输出，不能复制 handler；
- 每迁移一个旧路径，补一条“只执行一次”的回归测试；
- 当连续两个版本没有调用方后，才删除旧入口，并在变更记录中标明删除版本。

统一流式协议为 `POST /v1/chat/stream`：`turn_start`、检索/工具/消息事件、`snapshot`、`turn_end`。断线恢复以 `run_id`、`turn_id` 和最后 SSE id 为边界，不在 UI 中自建事件 schema。

## 3. 依赖与配置分层

保持离线核心可运行，按 profile 分层：

| Profile | 必选 | 可选 |
| --- | --- | --- |
| `local` | SQLite、FAISS/字面检索、Streamlit | LLM、OCR、MCP |
| `api` | FastAPI、Uvicorn | Embed、Voice |
| `production` | PostgreSQL/RLS、Caddy、观测 | Qdrant、Redis、MinerU |

新增组件必须同时提供：禁用状态、配置状态、就绪状态和降级原因。配置检查不应初始化 LLM/OCR；可选组件失败不能阻断文本主链。

## 4. 历史兼容文件治理

根目录 shim、包内入口和文档历史文件分三类管理：

- 根目录 `app.py`、`main.py` 等只保留转发，不写业务；
- `artpm_agent/` 是唯一实现位置；
- `docs/archive/` 只读归档，文档首页和当前契约必须明确标记 `current`。

每次新增入口都要更新 `docs/architecture/CURRENT.md`、`PROJECT_STRUCTURE.md` 和 README 的路径表。归档文档不得被测试、运行时或新设计引用为契约。

## 5. 存储边界与隔离

所有业务读写必须携带可信 `tenant_id/workspace_id`。工作区创建和会话外键由 `ConversationStore` 负责；知识资源和规则由 `WorkspaceKnowledgeStore` 负责；Session 事件不写入对话消息表。向量索引重建不能改变正确性，缓存删除不能改变结果。

检索边界固定为：

```text
trusted scope -> RetrievalPlan -> bounded search -> dedup/fusion -> citation
```

任何新检索后端必须实现同一 DTO，并通过 `confidence_floor`、limit、文本长度和 workspace scope 限制资源消耗。

## 6. WeKnora/Pi 能力取舍

- 采用 WeKnora 的工作区选择、知识范围、检索引用和 RAG 进度表达；不复制其多后端向量、GraphRAG、IM 和重型部署栈。
- 采用 Pi 的 typed event、可恢复 snapshot、工具边界和 session replay；不引入完整 durable harness、Chord/Facet/RPC 体系。
- Embed 采用独立 channel、短期 HMAC session、Origin allowlist、限流和 CSP；发布令牌永不进入浏览器。

## 7. 交付门禁

每次架构变更至少验证：
1. `pytest -q -m "not integration and not slow and not benchmark"`；
2. `ruff check artpm_agent tests --select E9,F63,F7,F82` 与
   `python -m compileall -q artpm_agent`；改动 Python 文件再按正常规则集做
   changed-surface 检查；
3. scope 隔离、空检索、附件幂等和兼容 facade 回归；
4. `/ready`、`/v1/workspaces`、`/v1/search`、`/v1/chat/stream` 实测；
5. Streamlit 首屏可见工作区选择器，切换后不会复用旧会话或审批状态。
6. `python scripts/mypy_ratchet.py`：`runtime/`、`harness/`、`api/`、`tenancy/`
   使用 `--strict --follow-imports=silent` 且无诊断。
7. `python scripts/build_graph.py --selftest`，并对改动模块用 `rg` 独立复核影响面。
