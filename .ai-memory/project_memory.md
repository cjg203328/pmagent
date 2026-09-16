# ArtPM Agent Project Memory

## Glossary

暂无项目术语漂移记录。

## Lessons Learned

### Known Issue: api.anthropic.li 模型目录可用但推理端无响应

**发现日期**: 2026-09-11
**问题类型**: 第三方依赖风险
**严重度**: 严重

#### 现象

TLS 校验正常，鉴权 `/v1/models` 返回 200 且包含 `qwen3.8-27b-uncensored`，但 `/chat/completions` 在 45 秒和 120 秒超时窗口内均未返回响应头。

#### 根因

已排除本机 TLS 信任链、模型标识缺失和基础鉴权问题。故障边界位于外部网关的推理路径；其内部排队、模型实例或账号侧状态无法从本地进一步确认。

#### 规避方案

先用模型目录确认基础连通性，再用单次、有限时的最小流式请求验证推理。达到 120 秒上限后停止重试，不关闭 TLS 校验，也不继续扩大客户端超时；等待上游恢复或切换到已确认可推理的兼容网关。

#### 相关文件

- `.env`
- `artpm_agent/utils/langchain_client.py`
- `artpm_agent/utils/llm_client.py`

#### 状态

未修复，外部阻塞。

## Decision Record: 生命周期事件与遗留外键迁移

**日期**: 2026-09-16
**问题**: 生产入口同时启用实时 EventBus 和 SessionStore 时，回合生命周期事件不能丢失；遗留表外键类型必须与整数主键一致。

### 决策

**选择**: EventBus 发布与 SessionStore 追加分开执行、分开容错；通过 Alembic `d4e5f6a7b8c9` 在升级时校验并转换四个遗留外键列。
**理由**: 实时观测故障不能阻断 durable audit；显式迁移才能修复已在旧 head 的数据库，且非整数历史值必须人工清理而不能静默截断。
**撤销条件**: 若所有宿主统一接入带会话作用域的 durable EventBus sink，或遗留表被正式下线并完成数据归档，可重新评估实现。

## Known Issue: 外部发布服务未在本机验证

**发现日期**: 2026-09-16
**问题类型**: 环境边界
**严重度**: 提示

### 现象

本机无 Docker 命令，PostgreSQL RLS 测试因未配置 `ARTPM_TEST_POSTGRES_URL` 跳过。

### 规避方案

CI 中运行 `docker-smoke` 和 `postgres-rls`；本地继续使用临时 SQLite 迁移验证和明确的集成 skip。

### 状态

待外部环境验证。

## Decision Record: Canonical runtime, storage registry and derived vector outbox

**日期**: 2026-09-17
**问题**: API/UI/CLI independently assembled services, UI session state acted as a dependency container, and vector synchronization failures were only process-local.

### 决策

**选择**: One process `RuntimeFactory` owns one Agent and one `StorageRegistry`; workspace coordinators are keyed by tenant/workspace/profile. `WorkspaceKnowledgeStore` schema v6 writes `knowledge_index_outbox` transactionally and `KnowledgeVectorProjector` owns derived vector updates.
**理由**: This removes per-turn Store/engine construction, preserves tenant isolation, allows unrelated workspaces to execute concurrently, and makes index lag durable and recoverable.
**Trade-offs**: Optional learning services and document/model adapters are initialized on first capability use. A pending outbox deliberately degrades search to literal retrieval until projection succeeds.
**撤销条件**: Replace only when a durable external event bus/projector provides the same atomic write, replay, rebuild and tenant-scope guarantees.

## Convention: Dependency profiles

Base dependencies are the Runtime/CLI profile. UI, documents, vector-local, provider SDKs, LangChain, MCP, OCR, MinerU, voice and observability remain optional extras. The `dev` extra contains quality tools only; full test environments explicitly install `production,dev`. Archived documents are never treated as current dependency or architecture contracts.

## Decision Record: 工作流租户适配与 UI 学习服务生命周期

**日期**: 2026-09-16
**问题**: API 与 Streamlit 分别创建工作流代理、工作流引擎和学习存储，导致租户绑定逻辑重复、UI 重跑触发重复 SQLite 初始化，并让兼容路由在缺少 `for_tenant()` 时崩溃。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
| --- | --- | --- | --- |
| 宿主各自维护代理和引擎 | 局部改动少 | 重复逻辑、容易产生租户边界漂移和重复初始化 | 中 |
| 共用 `ScopedWorkflowAgent`，由 `WorkflowCoordinator` 持有唯一引擎 | API/UI 作用域一致，减少重复对象与初始化 | UI 需保留旧会话路由兼容分支 | 低 |

### 决策

**选择**: API/UI 共用 `ScopedWorkflowAgent`；`WorkflowCoordinator` 负责内置工作流初始化并暴露唯一 `engine`；API 强制 `for_tenant()`，UI 仅对不支持该方法的会话内兼容路由回退原实例；EpisodeStore 与 ConsolidationScheduler 按 Streamlit 会话缓存。
**理由**: 收敛租户作用域和引擎事实源，同时消除每回合重复 SQLite schema 初始化，不改变旧测试代理和本地扩展的行为。
**Trade-offs**: UI 兼容分支无法为旧路由新增其本身不具备的多租户能力，因此只允许在已有会话上下文内使用；外部 API 路径不采用该回退。

### 影响范围

- `artpm_agent/workflows/coordinator.py`
- `artpm_agent/api/services.py`
- `artpm_agent/ui_helpers.py`
- `artpm_agent/ui_state.py`
- `artpm_agent/views/chat.py`

### 撤销条件

当所有 UI 注入路由都实现强制租户绑定协议，并且学习服务由统一的进程级依赖容器管理时，可删除 UI 兼容回退和 Streamlit session cache。

## Decision Record: Memory lifecycle and leased vector projection

**日期**: 2026-09-17
**问题**: The knowledge authority had a durable outbox but no explicit retention, deletion/export/offboarding contract, worker lease, bounded retry or dead-letter visibility.

### 决策

**选择**: Schema v7 keeps outbox rows in `pending/processing/done/dead`, uses atomic leases and exponential retry, and exposes queue/dead-letter/lag metrics. `MemoryLifecycleService` owns export, safe compaction, exact-confirmation deletion, tenant offboarding and vector rebuild tombstones. Current knowledge and accepted rules never expire automatically.
**理由**: Authority deletion and derived-index cleanup become independently observable; concurrent projectors cannot duplicate the same claim; a failed vector backend cannot be mistaken for completed tenant erasure.
**Trade-offs**: Completed outbox rows are retained for 30 days before compaction, and tenant offboarding may return `index_cleanup_pending` until the vector backend recovers.
**撤销条件**: Replace only with an external queue/lifecycle service that preserves atomic enqueue, scoped export/deletion, lease recovery, dead-letter visibility and rebuildability.

## Decision Record: Focused chat visual fragments

**日期**: 2026-09-17
**问题**: The Streamlit landing screen was visually sparse, while new page rules risked further expanding the 2,600-line `ui_style.py` compatibility source.

### 决策

**选择**: Keep `ui_style.py` as the single compatibility injection facade, place new chat visuals in `artpm_agent/ui/style_chat.py`, and keep state-independent welcome markup in `views/chat_welcome.py`.
**理由**: This improves the desktop/mobile experience without adding frontend dependencies, changing Runtime boundaries, or putting new page behavior into the legacy facade.
**Trade-offs**: The legacy base stylesheet is still physically large and remains a later extraction target; focused fragments are concatenated into one Streamlit style payload for stable reruns.

### 影响范围

- Chat empty state, suggestion actions and composer geometry.
- UI design-system documentation and changed-surface regression checks.

### 撤销条件

Replace the concatenated fragments when Streamlit exposes a stable scoped stylesheet or component theming API that preserves the current browser contract.

## Decision Record: Verified artifact delivery contract

**日期**: 2026-09-17
**问题**: Explicit natural-language file requests could fall through to ordinary model chat, and successful generation did not prove that a delivered Office/PDF file reopened with the requested content.

### 决策

**选择**: The canonical Harness artifact handler owns DOCX, XLSX, PPTX and PDF delivery. Simple explicit content is planned locally; complex content uses a strict bounded JSON plan. Each format is reopened and checked against the plan before atomic versioned publication, and the published copy must match size and SHA-256 before UI metadata carries `verification.status=passed`.
**理由**: A prose answer is not a valid substitute for a requested file. Format-level verification turns model intent into an auditable delivery contract while preserving workspace path, no-overwrite and optional dependency boundaries.
**Trade-offs**: Runtime verification proves openability, content and structure, not human visual approval of arbitrary complex layouts. PPTX/reportlab remain in the optional `documents` profile and are imported on capability use.
**撤销条件**: Replace only with a richer artifact runtime that preserves strict plans, safe versioned publication, format reopen checks, content assertions and canonical Harness/UI metadata behavior.
