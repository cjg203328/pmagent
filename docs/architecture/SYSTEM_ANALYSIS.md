# ArtPM Agent 系统分析拆解

本文是对当前代码库的一次完整拆解，覆盖六个层面：功能总结、设计思路、架构流程、
意识编排、意图识别、检索路径。所有结论均以当前仓库代码为准，并附 `文件:行号` 引用，
便于逐条核对。

阅读前提：本文描述的是**当前实现状态**，不是规划蓝图。规划类内容见
[`../roadmaps/`](../roadmaps/)，运维边界见
[`../operations/CURRENT_STATUS.md`](../operations/CURRENT_STATUS.md)。

---

## 目录

1. [功能总结](#1-功能总结)
2. [设计思路](#2-设计思路)
3. [架构流程](#3-架构流程)
4. [Agent 意识编排](#4-agent-意识编排)
5. [意图识别](#5-意图识别)
6. [检索路径解析](#6-检索路径解析)
7. [实现与文档差异](#7-实现与文档差异)
8. [一句话总结](#8-一句话总结)

---

## 1. 功能总结

### 1.1 产品定位

面向**游戏美术外包项目管理**的 AI 助手，采用离线优先设计。核心业务技能与本地文件
能力**不依赖任何 API Key**；配置 LLM 后额外获得通用对话、语义理解和模型工具调用能力
（`README.md:1-13`）。

### 1.2 四种入口

| 入口 | 端口 | 定位 | 代码位置 |
| --- | --- | --- | --- |
| **React UI** | `1501` | 新版主界面：对话、会话归档/删除、三档审批、网关配置 | `frontend/` |
| **Streamlit UI** | `8501` | 现行界面：对话、设置、可观测、工作流 | `artpm_agent/app.py` |
| **FastAPI 网关** | `8765` | 外部系统集成、健康检查、API 文档 | `artpm_agent/api/__main__.py:24-42` |
| **CLI** | 无端口 | 交互式 REPL | `artpm_agent/main.py:93-192` |

前端固定端口并开启 `strictPort`，`/api` 代理到网关并重写掉前缀
（`frontend/vite.config.ts:19-35`）。

### 1.3 技能清单（19 项）

业务技能 14 项（`artpm_agent/skills/skill_router.py:811-916`）：

| 技能 | 用途 | 风险 | 审批 |
| --- | --- | --- | --- |
| `document_classifier_parser` | 文档识别分类与结构化抽取 | low | 免审批 |
| `quote_calculator` | 报价利润、毛利率、净利率、风险测算 | low | 免审批 |
| `task_allocator` | 按环节匹配人员生成任务分配方案 | low | 免审批 |
| `progress_tracker` | 进度跟踪、延期预警、记录 | low | 免审批 |
| `reminder_bot` | 生成催办消息预览，不外部发送 | low | 免审批 |
| `reminder_dispatch` | 发送已批准催办，幂等不重试 | **high** | **需审批** |
| `quality_control` | 质检评审、通过/驳回/返工状态机、评分 | medium | 需审批 |
| `requirements_assessment` | 需求评估、复杂度判定、范围确认、建项目 | medium | 需审批 |
| `cost_control` | 人天成本推导、预算跟踪、超支告警 | medium | 需审批 |
| `quote_scheduling` | 人天估算、排期时间线、里程碑计划 | medium | 需审批 |
| `progress_management` | 里程碑视图、阻塞卡点、站会摘要 | medium | 需审批 |
| `delivery` | 交付清单、验收单、版本记录 | medium | 需审批 |
| `retrospective` | 结项报告、经验教训沉淀 | medium | 需审批 |

本地文件技能 5 项（MCP 只读，`artpm_agent/skills/mcp_skills.py`）：
`file_reader`、`file_search`、`data_analyzer`、`trend_analyzer`、`project_evaluator`。
前 5 项中 `file_reader/file_search/data_analyzer/trend_analyzer/project_evaluator` 被标记为
只读低风险；其余动态 MCP 技能统一标记 `untrusted`、`requires_approval=True`、
`side_effects_allowed=False`（`skill_router.py:921-950`）。

### 1.4 离线能力

| 能力 | 说明 |
| --- | --- |
| 报价与成本 | 报价、成本、利润、管理费、税费、风险测算 |
| 项目执行 | 任务分配、负载分析、进度追踪、截止预警 |
| 交付流程 | 排期、需求评估、质检、交付、复盘 |
| 文档处理 | 分类、结构化抽取；Excel/PDF/TXT/CSV/JSON 读取 |
| 本地分析 | 文件搜索、数据分析、趋势分析、项目健康度 |
| 通知投递 | 生成提醒，可选企业微信 Webhook |

### 1.5 配置 LLM 后增强

- 通用自然语言对话与语义理解
- 多 Provider 故障转移、请求限时、响应缓存
- DeepSeek `deepseek-chat`/`deepseek-reasoner`，独立捕获 `reasoning_content`
- **视觉模型搭配**：主模型不支持图片时路由到独立视觉模型
- 模型工具调用：参数先过 JSON Schema，写入型仍需宿主审批

### 1.6 可扩展能力

| 能力 | 启用方式 | 默认状态 |
| --- | --- | --- |
| MinerU 多模态解析 | `.[mineru]` / `.[mineru-client]` | 未部署则回退内置解析器 |
| OCR | `.[ocr]` | 按需安装 |
| 实时语音 | `.[voice]` + LiveKit | 文本通道不依赖 |
| Skills Forge MCP | `MCP_ENABLED=true` | **默认关闭**，命令执行另需开关 |
| Redis | `REDIS_URL` | 可选加速层，SQLite 仍权威 |
| Qdrant | `QDRANT_URL` | 无则用本地 FAISS |
| PostgreSQL/RLS | `DATABASE_URL` | 无则用 SQLite |

---

## 2. 设计思路

### 2.1 离线优先，降级成链

系统在**每一个外部依赖点**都设计了确定的回退路径，而不是"依赖不可用就报错"：

```text
意图识别   : keyword(离线) → embedding(离线) → LLM(需 Key) → 澄清
文档解析   : MinerU → 内置解析器(pdfplumber/docx/xlsx) → OCR
向量检索   : Qdrant → FAISS(本地) → 关键词兜底
响应缓存   : L1 内存 → L2 Redis(可选) → 直连模型
模型调用   : 主模型 → 备用模型(冷却 60s) → 视觉专用模型
访问模式   : 读取失败/缺失/非法 → controlled(最严默认)
租户校验   : 无法保范围的旧后端 → fail closed 拒绝
```

这条原则的直接收益：**没有 API Key 时系统依然可用**，只是失去语义理解。

### 2.2 唯一主链 + 兼容 facade

`harness.turn_service.run_turn()` 是 API 与 UI 的**唯一规范边界**
（`artpm_agent/harness/turn_service.py:414-520`）。`ArtPMAgent.chat()`、
`stream_chat()`、`RequestOrchestrator.stream_events()` 仅保留兼容用途，新业务逻辑
**不得**继续放回这些 facade（`docs/architecture/EXECUTION_MAP.md:29-31`）。

设计意图是把"编排"从"实现"里剥出来：`TurnContext` 承载输入，`TurnServiceBundle`
承载依赖，`TurnResult` 承载输出，三者构成可测试的契约边界
（`turn_service.py:150-217`）。

### 2.3 模型不能自授权

这是全系统最核心的安全原则，体现在三处：

1. **访问模式由宿主写入**，模型不能自选（`artpm_agent/security/access_mode.py:1-6`）。
2. **风险地板不可下调**：`effective_access_risk()` 对破坏性动作施加不可降低的
   风险下限；`critical`/`untrusted` 强制 `required_role="admin"`
   （`access_mode.py:46-70`、`artpm_agent/security/permission_store.py:986-989`）。
3. **权限门永远阻断**：`permission_preflight()` 创建持久请求后**始终返回阻断**，
   由受信宿主稍后一次性消费（`artpm_agent/security/permission_gate.py:1-8, 41-144`）。

### 2.4 失败即收紧（fail-closed）

所有不确定路径都选择"拒绝"而非"放行"：

- 权限存储不可用 → 阻断，不隐式放行（`permission_gate.py:91-96`）
- 服务器策略不可用 → 视为拒绝（`permission_gate.py:29-38`）
- 租户上下文构造异常 → 401 `invalid_tenant_context`（`api/app.py:175-181`）
- 工作区不属该租户 → 403 `workspace_tenant_mismatch`（`api/app.py:218-248`）
- 生产环境缺共享密钥 → 启动即抛 `RuntimeError`（`api/app.py:451-462`）
- 旧后端无法范围化查询 → 抛错而非泄漏默认工作区（`harness/memory_retrieval.py:517-530`）

### 2.5 记忆是证据，不是指令

注入模型的记忆一律带**来源标签**，而不是当作无条件事实：

```text
[事实][来源:workspace_knowledge/42；版本:3；时间:...；置信度:0.82] 客户偏好二次元风格
[规则][来源:workspace_rule/7；状态:已采纳] 报价不得低于成本 1.3 倍
[待确认][来源:long_term_memory/15] 上次提到交付期是 11 月
[建议][来源:reflection/9][状态:已采纳] 报价类问题优先给出区间而非单点
```

状态为 `draft/pending/rejected/conflict/superseded` 的记录会被直接跳过
（`harness/memory_retrieval.py:182-187, 258-270`）。

### 2.6 副作用必须审批，且一次性消费

写入型能力走完整状态机：`pending → approved → executing → completed/failed`
（`permission_store.py:27-42`）。关键机制：

- **幂等键**：`tool:{conversation_id}:{turn_id}:{call_id}:{tool.name}`
  （`permission_gate.py:118`），配合 `UNIQUE(workspace_id, idempotency_key)` 约束
  （`permission_store.py:477`）
- **动作绑定**：`action_sha256` 不一致则抛 `PermissionBindingError`
  （`permission_store.py:1052-1064`）
- **CAS 迁移**：所有状态变更带 `state_version + 1` 与 `expected_version`
  （`permission_store.py:1301-1322`）
- **一次性认领**：`claim_execution()` 后不能重复执行（`permission_store.py:1407-1444`）

---

## 3. 架构流程

### 3.1 系统全景

```text
┌──────────────────────────────────────────────────────────────────────┐
│                            客户端入口层                                │
│  React UI (1501)   Streamlit (8501)   REST API (8765)   CLI (stdio)   │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                v
┌──────────────────────────────────────────────────────────────────────┐
│                          网关与身份层                                  │
│  TrustedHeaderIdentityResolver   (services.py:137-196)                │
│    · x-workspace-id / x-actor-id / x-actor-role / x-tenant-id         │
│    · x-gateway-token  ← 生产强制 compare_digest 常量时间比对            │
│    · 本地仅接受回环 + 无转发头                                         │
│  TenantContextManager (contextvars)  → 请求级租户上下文                │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                v
┌──────────────────────────────────────────────────────────────────────┐
│                     唯一编排边界 harness.run_turn()                    │
│  TurnContext(user_input, history, attachments, scope, services)       │
│  TurnServiceBundle(profile/knowledge/artifact/workflow/feedback/...)  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
        ┌───────────────────────┴───────────────────────┐
        │          Handler 链（None = 未命中，继续）      │
        │  Step 0  记忆注入（对话摘要+长期记忆+规则+偏好）│
        │  H1      画像变更提案                          │
        │  H2      知识入库提案                          │
        │  H3      工作区规则提案                        │
        │  H4      产物生成                              │
        │  H5      持久化工作流路由                      │
        │  H6      意图 → 技能 → 审批 → 执行             │
        │  H7      模型兜底（终态，必定返回）             │
        └───────────────────────┬───────────────────────┘
                                │
                                v
┌──────────────────────────────────────────────────────────────────────┐
│                          结果与学习尾部                                │
│  TurnResult → 渲染 → 消息持久化                                       │
│  complete_turn_lifecycle(): 反馈 → episode → 反思 → 巩固              │
└──────────────────────────────────────────────────────────────────────┘

          支撑存储（全部按 tenant/workspace 隔离）
  ┌────────────┬────────────┬────────────┬────────────┬────────────┐
  │ SQLite     │ FAISS/     │ 工作区知识  │ 权限库      │ 工作流库    │
  │ 会话/消息   │ Qdrant     │ 库          │ (审批状态机)│ (定义/运行) │
  └────────────┴────────────┴────────────┴────────────┴────────────┘
```

### 3.2 请求全链路（以 `POST /v1/chat` 为例）

```text
客户端 POST /v1/chat  {message, conversation_id?, attachments?}
  │
  ├─ 1. 中间件：生成/回传 x-request-id                    (app.py:495-506)
  ├─ 2. _principal：解析身份头 → RequestPrincipal → tenant_context
  │                                                      (app.py:165-181)
  ├─ 3. _require_workspace：workspace 必须属该租户        (app.py:218-248)
  ├─ 4. 会话创建或复用（无 id 则新建）                    (app.py:945-1098)
  ├─ 5. 附件解析（附件 id 必须服务端签发）                (app.py:826-879)
  ├─ 6. 构造 TurnContext + TurnServiceBundle
  ├─ 7. run_turn()  ────────────────────────────────┐
  │                                                  │
  │   ├─ get_turn_scope() 认证范围（失败→拒绝）      │  (turn_service.py:443-448)
  │   ├─ emit TURN_START                            │
  │   ├─ Step 0  inject_memory_context()            │  (turn_service.py:322-327)
  │   ├─ H1..H7 handler 链                          │  (turn_service.py:329-411)
  │   ├─ complete_turn_lifecycle()                  │  (turn_service.py:466-474)
  │   └─ emit TURN_END                             │
  │                                                  │
  ├─ 8. 消息持久化（user + assistant，唯一键        (conversation_store.py:237-254)
  │      (conversation_id, turn_id, role)）
  └─ 9. 返回 {conversation_id, turn_id, response, success}
```

### 3.3 存储拓扑

| 存储 | 默认路径 | 关键表 | 代码 |
| --- | --- | --- | --- |
| 会话/消息 | `./data/conversations.db` | `workspaces`、`conversations`、`messages`、`conversation_access_grants` | `memory/conversation_store.py:192-254` |
| 会话日志 | 同上 | `session_entries`（可回放） | `memory/session_store.py:150-172` |
| 权限 | 同上 | `permission_requests` | `security/permission_store.py:439` |
| 工作流 | 同上 | `workflow_definitions/runs/steps/approvals/events` | `workflows/store.py:149-254` |
| 长期记忆 | `MEMORY_DB_PATH` | `documents` | `memory/memory_manager.py` |
| 向量索引 | `./data/vector_store` | `index.faiss` + `metadata.json` + `manifest.json` | `memory/faiss_vector_store.py:54-57` |
| 巩固状态 | `./data/consolidation.db` | — | `memory/consolidation.py:342-349` |
| 反思状态 | `./data/reflection.db` | — | `evolution/scheduler.py:219` |

SQLite 连接池：`pool_size=5`、`max_overflow=10`、`pool_recycle=3600`、`pool_pre_ping=True`，
每连接设置 `journal_mode=WAL`、`synchronous=NORMAL`、`cache_size=-64000`
（`database/connection_pool.py:44-104`）。

### 3.4 租户隔离双层

```text
PostgreSQL 部署：策略级 RLS
  ENABLE/FORCE ROW LEVEL SECURITY
  POLICY artpm_tenant_workspace_isolation
    USING (tenant_id = current_setting('app.tenant_id', true)
       AND workspace_id = current_setting('app.workspace_id', true))
                                        (alembic/versions/0003_postgresql_rls.py:16-63)
  事务开始注入：set_config('app.tenant_id'/'app.workspace_id', ..., true)
                                        (database/tenant_session.py:21-33)

SQLite 部署：ORM/Store 边界兜底
  before_flush   : 强制新行 tenant/workspace 与上下文一致  (tenant_session.py:35-48)
  do_orm_execute : 所有 SELECT/UPDATE/DELETE 追加
                   with_loader_criteria(TenantScopedMixin, ...)  (tenant_session.py:50-83)
  WorkspaceStoreGuard : 无显式 workspace_id 的方法直接抛
                   UnsafeStoreOperation           (tenancy/scoped_store.py:30-41)
```

---

## 4. Agent 意识编排

"意识编排"指 Agent 在一轮对话中**如何感知、决策、执行、记忆**的完整闭环。
系统的编排不是"一个循环调模型"，而是**一条有优先级的 handler 链 + 一条学习尾部**。

### 4.1 一轮对话的七个阶段

```text
                    ┌─────────────────────────┐
                    │  ① 范围认证             │
                    │  get_turn_scope(ctx)    │  失败 → tenant_scope_gate 拒绝
                    └───────────┬─────────────┘
                                v
                    ┌─────────────────────────┐
                    │  ② 记忆唤醒 (Step 0)    │  幂等，每轮一次
                    │  对话摘要 + 长期记忆     │  (turn_service.py:319-327)
                    │  + 工作区规则 + 偏好     │
                    │  + 进化策略 + 元记忆     │
                    └───────────┬─────────────┘
                                v
        ┌───────────────────────────────────────────────────┐
        │  ③ 意图决策链（按优先级短路）                      │
        │                                                    │
        │  H1 画像变更提案    → 命中即返回                   │
        │  H2 知识入库提案    → 命中即返回（附件优先）        │
        │  H3 工作区规则提案  → 命中即返回                   │
        │  H4 产物生成        → 命中即返回（xlsx/docx）       │
        │  H5 工作流路由      → 命中即返回（可暂停待审批）    │
        │  H6 技能路由        → 命中即返回（含审批门）        │
        │  H7 模型兜底        → 终态，必定返回               │
        └───────────────────┬───────────────────────────────┘
                            v
                    ┌─────────────────────────┐
                    │  ④ 结果封装 TurnResult  │
                    │  response/metadata/      │
                    │  awaiting_approval/      │
                    │  artifacts/handled_by    │
                    └───────────┬─────────────┘
                                v
                    ┌─────────────────────────┐
                    │  ⑤ 学习尾部（best-effort）│
                    │  反馈 → episode → 反思   │
                    │  → 知识巩固             │
                    └───────────┬─────────────┘
                                v
                    ┌─────────────────────────┐
                    │  ⑥ 事件记录             │
                    │  TURN_START / TURN_END  │
                    └───────────┬─────────────┘
                                v
                    ┌─────────────────────────┐
                    │  ⑦ 消息持久化           │
                    └─────────────────────────┘
```

**关键设计**：handler 返回 `None` 表示"未命中，交给下一个"，返回 `TurnResult`
表示"已处理，短路"。这让"新能力接入"变成"插一个 handler"，而不需要改动主流程
（`turn_service.py:273-277`）。

### 4.2 优先级背后的意图

handler 顺序不是随意的，体现了明确的优先级判断：

| 顺序 | Handler | 为什么在这个位置 |
| --- | --- | --- |
| Step 0 | 记忆注入 | 所有后续 handler 都要消费 `ctx.knowledge_context`，必须最先 |
| H1 | 画像变更 | 显式"以后都这样回答"指令，优先级最高，避免被当普通问答 |
| H2 | 知识入库 | **必须早于附件解析**，因为它有自己的持久化提案载荷，不能重复解析同一批文件（`turn_service.py:356-359`） |
| H3 | 规则提案 | 显式"记住这条规则"，需在产物/技能之前拦截 |
| H4 | 产物生成 | 有明确格式诉求（xlsx/docx）时优先产出文件而非文本 |
| H5 | 工作流路由 | 多步骤确定性流程，优先于单技能 |
| H6 | 技能路由 | 单技能执行 + 审批门 |
| H7 | 模型兜底 | 通用对话，终态 |

### 4.3 学习闭环（意识的自进化）

这是"意识"层面最有意思的部分：系统会从历史回合中**自己提炼策略**并注入未来回合。

```text
   每一轮结束
        │
        ├─ record_outcome()  ──→ Episode（回合结果落库）
        │                         含 handler、成功率、用户反馈
        │
        ├─ FeedbackStore.add() ─→ 偏好/纠正（带 no_inject 标记控制是否注入）
        │
        └─ reflection_scheduler.run_if_due()  默认间隔 30 分钟
                │                              (evolution/scheduler.py:112)
                v
        ┌──────────────────────────────────────────────┐
        │  ReflectionJob.mine_episodes()               │
        │    聚合每 handler 成功率与反馈               │
        │    (evolution/reflection.py:112)             │
        └───────────────┬──────────────────────────────┘
                        v
        ┌──────────────────────────────────────────────┐
        │  generate_proposals()  触发阈值              │
        │    样本 ≥ 5 且失败率 ≥ 0.40 → 生成提案       │
        │    失败率 ≥ 0.60 → 升级 medium + routing 提案 │
        │    (evolution/reflection.py:177-207)         │
        └───────────────┬──────────────────────────────┘
                        v
        ┌──────────────────────────────────────────────┐
        │  apply_proposal()                            │
        │    → StrategyStore.add(Strategy(...))        │
        │    审批映射: low→none / medium→user /        │
        │             high|critical→admin              │
        │    (reflection.py:35, 278, 329-330)          │
        └───────────────┬──────────────────────────────┘
                        v
        ┌──────────────────────────────────────────────┐
        │  下一轮注入【优化策略（来自经验复盘）】       │
        │  (harness/memory_retrieval.py:806-831)       │
        └──────────────────────────────────────────────┘
```

配套的**元记忆**机制负责识别"我不知道什么"：

- 产出 `known / unknown / low_confidence / no_strategy` 四类缺口
- 给出 `search`（联网检索）或 `ask_user`（向用户澄清）建议
- 阈值：置信度 0.5、低召回 0.1（`evolution/meta_memory.py:94, 164-169`）
- **仅在确有缺口时追加**，避免无谓污染每轮上下文（`memory_retrieval.py:974-975`）

### 4.4 事件与可观测

事件按三个域分类（`runtime/events.py:38-49`）：

| 域 | 语义 | 用途 |
| --- | --- | --- |
| `SESSION` | 持久可回放 | 审计、重放、会话日志 |
| `AGENT` | 实时观测 | 流式 UI、进度展示 |
| `CAPABILITY` | 接缝通知 | 能力注册与变更 |

生命周期事件：`agent_start/end`、`turn_start/end`、`message_start/update/end`、
`tool_execution_start/update/end`、`runtime_error`（`events.py:22-35`）。

`TurnEventRecorder.emit()` 优先走 event_bus，否则落 SessionStore 作为持久兜底；
**记录失败只告警，绝不阻断用户请求**（`runtime/turn_events.py:13-23, 76-98`）。

### 4.5 意识编排的边界（重要）

必须明确：**这里的"意识"是工程意义上的编排闭环，不是模型自主意识**。

- 每一步决策都有确定性的判定依据（阈值、白名单、状态机），不是模型自由发挥
- 模型的输出**不构成授权**：访问模式、审批、风险等级全部由宿主与服务端策略决定
- 学习产出的 `Strategy` 只是**建议性文本**，注入时带 `[建议][状态:已采纳]` 标签
  （`memory_retrieval.py:825-828`），不改变任何权限判定

---

## 5. 意图识别

实现在 `artpm_agent/routing/service.py`，是一个**三层递进的判定器**，
目标是"高精度优先，宁可澄清也不误执行"。

### 5.1 三层结构

```text
用户输入
   │
   ├─ 路由缓存命中？(256 条 LRU，key = 去空格小写) ──→ 直接返回
   │                                          (service.py:382-385)
   v
┌──────────────────────────────────────────────────────────────┐
│ Tier 0：关键词（确定性，零成本，离线）                        │
│   INTENT_KEYWORDS 加权匹配：命中词长度累加                    │
│   (service.py:86-157, 720-733)                                │
│   严格门：必须同时命中 action 且 entity                       │
│   (service.py:609-610)                                        │
│   通过 → confidence 0.82~0.99                                 │
└──────────────────────────┬───────────────────────────────────┘
                           │ 未通过
                           v
┌──────────────────────────────────────────────────────────────┐
│ Tier 1：Embedding（离线语义）                                 │
│   INTENT_EXAMPLES 预计算嵌入（每技能 15-20 条真实说法）        │
│   字符 n-gram + MD5 特征哈希，1536 维，L2 归一化               │
│   (service.py:232-330；memory/embeddings.py:21-53)            │
│   余弦相似度取每技能最大值，要求 sim ≥ 0.25 且 margin ≥ 0.05   │
│   (service.py:353-354, 418-428)                               │
│   放行门放宽：sim ≥ 0.40 只需 action 或 entity 之一            │
│   (service.py:612-617)                                        │
└──────────────────────────┬───────────────────────────────────┘
                           │ 未通过
                           v
┌──────────────────────────────────────────────────────────────┐
│ Tier 2：LLM 分类（需 API Key）                                │
│   把技能描述表 + 用户消息交给 LLM，要求只返回模块名            │
│   (service.py:641-711)                                        │
│   前置条件：输入 ≥ 8 字符 且 llm_client 存在 且开关开启        │
│   (service.py:439-443)                                        │
│   放行门最宽：action 或 entity 之一即可                       │
│   (service.py:619-620)                                        │
└──────────────────────────┬───────────────────────────────────┘
                           │ 仍未通过
                           v
                   澄清路径（clarification）
```

### 5.2 双信号门：action + entity

这是意图识别精度的核心机制。每个技能定义了**动作词**和**实体词**两组信号
（`service.py:161-230`）。例如：

```python
"quote_calculator": {
    "actions":  ("计算", "算一下", "帮我算", "测算", "核算", "评估", "分析", "赚"),
    "entities": ("报价", "成本", "利润", "利润率", "毛利", "净利", "收益", ...),
},
```

**为什么需要双信号**：单看"状态"、"搜索"、"文档"这类宽泛词，日常对话中大量出现，
不应该触发工具执行。只有"动作 + 业务对象"同时出现，才判定为真实的执行意图
（`service.py:159-160`）。

同时设置了**概念问答豁免**：出现"是什么意思 / 什么是 / 概念 / 定义 / 怎么理解"
时直接判定为非执行意图（`service.py:571-575, 601-605`）——问"什么是利润率"不该去跑计算器。

### 5.3 执行门（execution gate）

意图识别与**是否允许执行**是两个独立步骤。`execution_gate()` 决定一个决策能否
进入技能执行（`service.py:486-499, 790-817`）：

| Tier | 置信度下限 | margin 下限 |
| --- | --- | --- |
| `keyword` | 0.80 | 0.05 |
| `embedding` | 0.65 | 0.05 |
| `llm` | 0.70 | 0.00 |
| `legacy` | 0.95 | 0.00 |

`explicit`（显式指定）时放宽：置信度下限降到 ≤ 0.55，margin 下限归零
（`service.py:810-812`）。

**关键约束**：`explicit` 只能来自**受信运行时决策**，不能来自客户端可伪造的
上下文字段——否则攻击者可以借此降低门槛
（`harness/skill_handler.py:279-281`）。

### 5.4 澄清路径

门未通过时，系统不会静默失败或强行执行，而是返回可操作的澄清提示：

```text
"我识别到可能涉及：报价计算、成本管控，但把握不足。请明确你希望执行的具体操作。"
                                            (skill_handler.py:298-318)
```

触发条件：`clarification_required` / `low_confidence` / `low_margin`。
`clarification_required` 在候选之间 margin 过小（< 0.05）时置位
（`service.py:401-405, 562-564`）。

### 5.5 IntentDecision：可审计的决策对象

每次判定返回一个不可变结构，便于审计与调试（`service.py:22-71`）：

```python
IntentDecision(
    intent="quote_calculator",     # 命中的技能，None 表示未命中
    confidence=0.93,               # 0.0~1.0
    tier="keyword",                # keyword|embedding|llm|none|error|legacy
    candidates=("quote_calculator", "cost_control", "project_evaluator"),
    margin=0.42,                   # 与第二名的相对差距
    clarification_required=False,
    explicit=False,
)
```

该对象会被写入 `TurnResult.metadata["intent_decision"]` 与 `TURN_END` 事件，
使"为什么走了这条路"可追溯（`skill_handler.py:277, 292-296`）。

---

## 6. 检索路径解析

检索发生在**每轮对话的 Step 0**，入口是 `inject_memory_context()`
（`harness/memory_retrieval.py:834-1023`）。目标是把"相关且可信"的记忆
装进 `ctx.knowledge_context`，供后续 handler 消费。

### 6.1 四路并行检索

```text
                    inject_memory_context(ctx)
                              │
        ┌─────────────────────┼─────────────────────┬──────────────────┐
        v                     v                     v                  v
┌───────────────┐  ┌──────────────────┐  ┌──────────────┐  ┌──────────────┐
│ TencentDB     │  │ 长期记忆          │  │ 工作区规则    │  │ 工作区资料    │
│ Agent Memory  │  │ MemoryManager    │  │ get_active_  │  │ knowledge_   │
│ recall()      │  │ .retrieve()      │  │ rules()      │  │ store.search │
│ (可选)        │  │ (向量检索)        │  │ (权威通道)    │  │ (向量检索)   │
└───────┬───────┘  └────────┬─────────┘  └──────┬───────┘  └──────┬───────┘
        │                   │                   │                 │
        └───────────────────┴─────────┬─────────┴─────────────────┘
                                      v
                        ┌──────────────────────────┐
                        │ 证据化渲染                │
                        │ _evidence_text()          │
                        │ 加 [事实/规则/待确认] 标签 │
                        └────────────┬─────────────┘
                                     v
                        ┌──────────────────────────┐
                        │ 去重（归一化文本 key）     │
                        └────────────┬─────────────┘
                                     v
                        ┌──────────────────────────┐
                        │ 预算裁剪（优先级 + 轮询）  │
                        └────────────┬─────────────┘
                                     v
                        ctx.knowledge_context
```

**规则独立通道的意义**：已采纳规则走独立检索路径，这样"某个模糊文档检索抛异常"
不会把"确认过的工作区约束"一起藏掉
（`memory_retrieval.py:457-459`）。

### 6.2 查询构造：只用用户输入

`_build_query()` 的规则值得单独说明（`memory_retrieval.py:92-123`）：

```text
查询 = 当前用户输入(≤800字) + 最近 2 条用户历史(各 ≤300 字)
       总长上限 1200 字
```

**刻意排除助手消息**。原因写在代码注释里：助手回复可能包含猜测或过期信息，
把它们当作检索词会**强化这些猜测**，并挤掉用户确认过的记忆
（`memory_retrieval.py:98-100`）。

### 6.3 三层过滤（可信度闸门）

每条候选记忆都要过三道过滤，任何一道不过就丢弃：

```text
① 置信度闸门   _passes_confidence(record, floor=0.05)
               confidence 为 None 或 ≥ 0.05          (memory_retrieval.py:318-320)

② 状态闸门     _record_provenance() → kind
               draft/pending/proposed/rejected/
               revoked/conflict/superseded → skip     (memory_retrieval.py:182-187, 258-260)

③ 过期闸门     _record_expired()
               expires_at/expiry/valid_until 已过 → 丢弃
               **解析失败也视为过期**（不静默信任）     (memory_retrieval.py:223-238)
```

第③条的"解析失败即过期"是典型的 fail-closed 设计。

### 6.4 向量检索细节

**Embedding**（`memory/embeddings.py`）：

| 项 | 值 |
| --- | --- |
| 算法 | 字符 n-gram（3/4/5）+ MD5 特征哈希 + L2 归一化 |
| 维度 | 默认 1536（下限 64） |
| 指纹 | `char-ngram-feature-hash-v1:{dimension}` |
| 是否联网 | **否，纯本地确定性** |

**后端选择**（`memory/vector_store.py:30-50`）：

```text
QDRANT_URL 非空  → 默认 Qdrant（cosine，collection=artpm_memory）
VECTOR_BACKEND   → 强制覆盖
Qdrant 初始化/运行异常 → 自动降回 FAISS
```

**FAISS 后端**（`memory/faiss_vector_store.py`）：

- 索引：`IndexIDMap2(IndexFlatIP)`，向量预归一化 → 等价 cosine
- 持久化：`index.faiss` + `metadata.json` + `manifest.json` + `write.lock`（跨进程锁）
- **filters 非空时全索引扫描再后置过滤**，避免按分区过滤造成召回丢失
  （`faiss_vector_store.py:349-352`）

**自适应后端**（`memory/adaptive_vector_store.py:25-172`）：

| 规模 | 索引类型 | 度量 |
| --- | --- | --- |
| < 1000 | `IndexFlatL2` | L2 |
| < 100000 | IVF | L2 |
| 其余 | HNSW | L2 |

**MemoryManager.retrieve() 流程**（`memory/memory_manager.py:174-289`）：

```text
1. _resolve_retrieval_scope()  强制注入受信 tenant/workspace
2. 向量检索 top_k*2 + filters  （TypeError 时回退旧式 search 签名）
3. 对返回 metadata 二次过滤
4. 按 id 回查 documents 表，校验 tenant/workspace 一致
5. 取前 top_k，score 用后端返回的距离/内积
6. 无向量结果时离线兜底：
     整串命中 +1.0；分词命中 term_hits/len(terms)；按 (-score, id) 排序
```

### 6.5 预算裁剪：公平轮询

`_format_retrieved_buckets()` 解决一个具体问题：**防止单一来源吃光整个预算**
（`memory_retrieval.py:657-720`）。

```text
预算默认：top_k=4, 单条 ≤800 字, 总计 ≤3200 字
                                    (memory_retrieval.py:41-45)

分配策略：
  ① 先为每个来源保留"最佳命中"的等额切片
     · 若所有来源的首条都能放下 → 各放一条
     · 若放不下 → 按来源数均分，各自截断加省略号
  ② 再按轮询（round-robin）填充第 2..top_k 条
     · 保留每个来源内部的排序
```

**注入优先级**（超预算时先丢谁，`memory_retrieval.py:1031-1051`）：

| 优先级 | 块 | 理由 |
| --- | --- | --- |
| 6 | 调用方提供的 `knowledge_context` | 用户确认的工作区事实，最高 |
| 5 | `【对话摘要】` | 承载轮内连续性 |
| 4 | `【用户偏好与纠正】` | 明确的行为纠正 |
| 3 | `【优化策略】` | 复盘学到的策略 |
| 2 | `【相关记忆】` | 模糊的长期召回 |
| 1 | `【元记忆】` | 知识缺口建议 |

裁剪时**先丢整块低优先级**，若只剩一块仍超预算则**就地截断**（保留片段而非全丢）
（`memory_retrieval.py:1054-1075`）。

### 6.6 偏好注入的降噪规则

`FeedbackStore` 的注入有一处精细设计（`memory_retrieval.py:778-800, 1184-1223`）：

- **裸 👎 记录但不注入**：没有具体原因的负面反馈标记 `no_inject=True`，
  只用于反思挖掘，不污染每轮上下文
- **带原因的 👎 才注入**：用户写了具体纠正文本，才作为 `[避免]` 偏好注入
- **👍 只记录不注入**：避免"保持这个风格"这类模糊指令与画像的
  `response_style` 冲突

---

## 7. 实现与文档差异

本次拆解中发现三处**代码与文档/注释不一致**。三者均已修复，状态见下表；
修复方案与验收标准见
[`../roadmaps/memory-retrieval-optimization-plan.md`](../roadmaps/memory-retrieval-optimization-plan.md)。

| # | 位置 | 差异 | 状态 |
| --- | --- | --- | --- |
| 1 | `artpm_agent/memory/` | 不存在 `vector_search.py`；向量检索实际由 `vector_store.py` + `faiss_vector_store.py` + `qdrant_vector_store.py` + `adaptive_vector_store.py` 承担 | ✅ 已勘误：`PROJECT_STRUCTURE.md` 补充实际文件清单；`optimization-roadmap.md`、`TECHNICAL_DEBT_TASKS.md`、`improvement-roadmap-24w.md` 的过时文件名已修正或标注 |
| 2 | `memory/conversation_compressor.py` | 类注释承诺"LLM 失败时退化为截断策略"，但异常分支只置 `result.error`，**未实现**截断降级 | ✅ 已修复：新增 `_degraded_summary()`，LLM 异常或返回空摘要时保留最近用户原话并标记 `degraded=True` |
| 3 | `memory/memory_manager.py` | `_get_embedding` 文档称"设置 llm_client 就会改用真实嵌入"，但代码从未读取该参数 | ✅ 已修复：新增 `onnx`/`remote` provider 并接入 `llm_client`；默认仍为本地哈希，配置错误时可见降级 |

另有一处需要留意但**不是缺陷**：`artpm_agent/plugins/capabilities.py` 的
`CapabilityEntry.metadata` 原用 `MappingProxyType({})` 作 dataclass 默认值，
在 Python 3.12 之前会被拒绝，已改为 `field(default_factory=...)` 以保持 3.10/3.11 兼容。

---

## 8. 一句话总结

ArtPM Agent 是一个**离线优先的美术外包项目管理 Agent**，其技术骨架可以概括为：

> **一条唯一编排主链**（`run_turn` + 7 级 handler 链）
> **三层意图识别**（关键词 → 离线嵌入 → LLM，双信号门控精度）
> **四路并行检索**（长期记忆 / 工作区规则 / 工作区资料 / 外部记忆，证据化 + 预算公平分配）
> **两层租户隔离**（PostgreSQL RLS + ORM 边界兜底，全路径 fail-closed）
> **一条学习闭环**（episode → 反思 → 策略 → 回注）

其设计哲学的统一线索是：**离线可用、失败收紧、模型不能自授权、副作用必须审批、
记忆是证据而非指令**。

---

## 附：核对入口

| 想了解 | 去哪看 |
| --- | --- |
| 一次请求的完整执行顺序 | [`EXECUTION_MAP.md`](EXECUTION_MAP.md) |
| 系统总体架构 | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| 详细架构图谱 | [`ARCHITECTURE_DIAGRAM.md`](ARCHITECTURE_DIAGRAM.md) |
| 记忆与自进化设计 | [`MEMORY_EVOLUTION_DESIGN.md`](MEMORY_EVOLUTION_DESIGN.md) |
| 多租户隔离 | [`MULTI_TENANT_ARCHITECTURE.md`](MULTI_TENANT_ARCHITECTURE.md) |
| 仓库目录与入口 | [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) |
| API 请求与错误码 | [`../operations/API_GATEWAY_DOCUMENTATION.md`](../operations/API_GATEWAY_DOCUMENTATION.md) |
| 当前已验证边界 | [`../operations/CURRENT_STATUS.md`](../operations/CURRENT_STATUS.md) |
| 优缺点与优化路线 | [`../analysis/project-strengths-risks-roadmap-2026-10-09.md`](../analysis/project-strengths-risks-roadmap-2026-10-09.md) |
