# ArtPM Agent 架构总结

> 整理时间：2026-07-14。基于当前工作树（含大量 WIP 改动）实测梳理。
> 演进蓝图见 `PI_ARCHITECTURE_ADOPTION.md`（借鉴 earendil-works/pi）。

## 1. 项目定位

面向**游戏美术外包项目经理**的、离线优先的 Streamlit AI 助手。把
「需求 → 报价 → 成本 → 排期 → 分配 → 进度 → 质检 → 交付 → 复盘」
全生命周期用 AI 串起来。核心差异化：**垂直领域闭环 + 离线可跑 + 确定性工作流护栏**。

## 2. 分层架构（当前）

```
┌─────────────────────────────────────────────────────────────┐
│  入口层  app.py(Streamlit) · main.py(CLI) · health_check.py   │
└───────────────────────────┬─────────────────────────────────┘
                            │  turn 请求
┌───────────────────────────▼─────────────────────────────────┐
│  UI 层  pages/chat.py · pages/settings.py · ui_helpers · ui_style │
│  （当前仍耦合：profile提案/知识摄取/artifact/工作流选择/持久化）│
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  运行时核心  runtime/  ← Pi Phase 1 + Loop 基础已落地           │
│  AgentRuntime · AgentLoop · ToolRegistry · typed events       │
│  stream_events() / stream_chat()（兼容事件协议）               │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  智能体大脑  agent.py (2267行·最大单体)                        │
│  意图路由(关键词→embedding→LLM) · 普通聊天 · 模型回退         │
│  ⚠ Pi Phase 3 目标：把编排逻辑抽到 run_turn() 应用服务        │
└──────┬───────────────┬──────────────────┬────────────────────┘
       │               │                  │
┌──────▼─────┐ ┌───────▼────────┐ ┌──────▼──────────────────┐
│ 技能系统   │ │ 工作流引擎      │ │ 适配器层（围绕运行时）  │
│ skills/    │ │ workflows/      │ │ memory/ db/ core(MCP)   │
│ skill_router│ │ engine/coord/  │ │ utils(llm)/ parsers/    │
│ 18个技能   │ │ selector/store │ │ artifacts/ profiles/    │
│            │ │ 审批护栏+幂等  │ │ visualization/          │
└────────────┘ └────────────────┘ └───────────────────────────┘
```

## 3. 入口与启动

| 文件 | 作用 |
|---|---|
| `app.py` | Streamlit 入口（launcher，已拆出 `pages/`） |
| `main.py` | CLI 入口（`/help` `/skills` `/quit`，调用 `ArtPMAgent.chat`） |
| `health_check.py` | 组件健康检查（imports / llm / vector / db / config / agent），输出 `logs/health_check.json`，unhealthy 时 `exit 1` |
| `Dockerfile` | 已存在（WIP 中修改） |

## 4. 运行时核心 `runtime/`（Pi Phase 1 与 Tool Loop 基础已落地）

- `AgentRuntime`（465 行）：兼容原字符串流的 run 状态机，拥有会话作用域、bounded 内存消息、协作式 `abort`、订阅屏障和确定性事件排序。
- `AgentLoop` + `tools.py`：多回合结构化工具调用、并行/串行执行、审批 preflight、完整 JSON Schema 校验和 SkillRouter 适配；已通过 `stream_events` 接入生产聊天链，默认开启并保留环境变量回滚。
- `AgentEvent / AgentMessage / AgentState`：`frozen` + `slots` 不可变快照。
- `ArtPMAgent.stream_events()` 暴露运行时协议；`stream_chat()` 向后兼容（过滤文本增量）。
- 这是设计的"小核心"，但 `agent.py` 仍把领域逻辑堆在核心里（见 §5）。

## 5. 智能体大脑 `agent.py`（2267 行，最大单体）

当前仍承担：**初始化 + 意图路由 + 附件处理 + 普通聊天 + 流式回复 + 模型回退 + Skill 输入提取与结果格式化**。
Profile 提案、知识摄取、artifact、工作流选择和持久化编排主要仍在 `pages/chat.py`；这正是 `PI_ARCHITECTURE_ADOPTION.md` Phase 3 要抽出的 `run_turn()` 应用服务。

## 6. 意图路由（三层，零成本优先）

```
关键词(INTENT_KEYWORDS) → embedding(离线 hash 向量) → LLM(可选语义兜底)
```
门禁（分层置信）：
- 关键词层：严格（action+entity 双命中），强命中（kw_score≥4）直接放行。
- embedding 层：sim≥0.40 放宽到 action OR entity；sim≥0.30 且有 entity 也放行。
- LLM 层：已做语义判断，action OR entity 即接受，valid 集合绑定 17 技能描述（杜绝遗漏）。

## 7. 技能系统 `skills/`

- `skill_router.py`（1045 行）注册表：`SKILL_REGISTRY / SKILL_METADATA / SKILL_ROUTE_SIGNALS`。
- 共 **18 个已注册技能**（含 `reminder_dispatch` 与 5 个本地 MCP 只读技能）。
- `BaseSkill` 子类模式。

## 8. 工作流引擎 `workflows/`（确定性、带审批护栏）

- `engine.py` + `coordinator.py` + `selector.py` + `store.py`（1196 行）+ `models.py` + `risk_policy.py` + `defaults.py`。
- `risk_policy.DEFAULT_SKILL_CAPABILITIES` 能力允许清单，作为**强制 preflight 钩子**（审批/幂等/工作区隔离/能力白名单不可被模型绕过）。
- 幂等键 `chat-turn-{turn_id}`、审批门 `awaiting_approval`、SQLite 投影。

## 9. 记忆与知识 `memory/`

- `workspace_knowledge_store.py`（2079 行，最大文件之一）：知识库 / 向量 / embedding 检索。
- `conversation_store.py`（779 行）：对话存储。
- `embeddings.py`：`DeterministicEmbeddingProvider`（离线 hash 向量，无 API 依赖）。
- `vector_store.py`：FAISS 本地索引。

## 10. 持久化 `database/`

- `models.py`（687 行）：SQLAlchemy 2.0 声明式，**8 张业务表**（Project / Asset / Task / Document / Delivery / AssetVersion / KnowledgeBase / …）。
- `migrate.py`：`ensure_schema` 安全接入 Alembic（已有表仅 `stamp head`，零 DDL / 零数据风险）；alembic 缺失回退 `create_all`。
- ⚠ **遗留孤儿表**：真实库 `data/artpm.db` 有 **14 张表**，其中 **6 张 code 未建模**（`staff / quotes / reminders / operation_logs / progress_updates / task_assignments`）—— schema 漂移技术债。

## 11. MCP 客户端 `core/`

- `MCPClient`（远程 Skills Forge HTTP）、`EnhancedMCPClient`（本地工具）、`UnifiedMCPClient`（统一入口，暴露 `list_skills / call_skill / enabled`）。
- `agent.py` 已统一走 `get_unified_mcp_client()`。

## 12. UI 层 `pages/` + `ui_*`

- `pages/chat.py`（601 行）：聊天 UI，仍耦合 profile 提案 / 知识摄取 / artifact / 工作流选择（Phase 3 要搬走）。
- `pages/settings.py`、`pages/dead_pages.py`（`dead_pages` 疑似废弃页）。
- `ui_helpers.py`（1303 行）、`ui_style.py`（896 行）：UI 辅助与样式。

## 13. 工具 / 解析 / 产物 / 画像

- `utils/`：`llm_client`（提供商门面）、`unlimited_ocr`（785）、`ocr_runtime`（592）、`chat_intent`、`cache`、`metrics`、`data_masking` 等。
- `parsers/`：`ocr_parser`（376）、`excel_parser`（366）文档解析。
- `artifacts/`（509 + 403）：产物生成 coordinator / generator。
- `profiles/`（586 + 394 + 245）：`AgentProfilePatch` / `QuotePolicyPatch` 画像补丁。
- `visualization/`（534）：高级图表。

## 14. 测试与质量

- 全量 **547 passed / 2 skipped / 0 failed**。
- 覆盖率 **74.60%**，通过仓库 `--cov-fail-under=50` 门禁。
- `pytest.ini` 同时加载 `artpm_agent` 与仓库根，测试不再依赖模块导入顺序。
- ruff 护栏。
- 注意：大量测试文件处于 WIP（未提交修改），提交需谨慎。

## 15. 关键指标与技术债

| 文件 | 行数 | 备注 |
|---|---:|---|
| `agent.py` | 2267 | 单体，Phase 3 拆分目标 |
| `memory/workspace_knowledge_store.py` | 2079 | 职责过重 |
| `ui_helpers.py` | 1303 | 职责过重 |
| `workflows/store.py` | 1196 | 职责过重 |
| `skills/skill_router.py` | 1045 | 职责过重 |

**技术债清单（按严重度）：**

1. **大文件集中**：5 个 >1000 行文件，单体职责过重、难测试、易回归。
2. ~~**遗留孤儿表 6 张**~~：✅ **已解决** - 所有表已在 models.py 建模（lines 679-755），Alembic 迁移 `b1c2d3e4f506` 已应用。
3. ~~**嵌套重复包 `artpm_agent/artpm_agent/`**~~：✅ **已清理** - 嵌套目录已删除（2026-07-14）。
4. **`agent.py` / `pages/chat.py` 仍耦合领域编排**：Pi Phase 3 Stage 2 完成（Profile/Knowledge 已分离），Stage 3-4 待继续。
5. **Pi Phase 2 基础已完成，Phase 4 / 5 未启动**：Provider Adapter、工具 Schema 与 append-only 回放已落地；审批恢复、压缩和扩展诊断仍待完成。
6. ~~**离线降级文档缺失**~~：✅ **已完成** - `OFFLINE_FALLBACK.md` 已扩展到生产级（11 节，500+ 行），README.md 已集成。
7. **连接生命周期告警**：覆盖率运行仍报告 SQLite `ResourceWarning`，后续应为数据库管理器和测试 fixture 增加统一 `close/dispose`。

## 16. Pi 架构演进进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | provider-neutral 运行时核心 + 事件协议 | ✅ 已落地（runtime/） |
| 包边界 | 让 `artpm_agent.*` 成为规范安装 API | 🟡 部分（有 `__init__.py`，扁平模块仍并存） |
| Phase 2 | Provider Adapter、统一工具契约与低层 Loop | ✅ 已完成并正式启用 |
| Phase 3 | Harness、append-only Session 与 `run_turn()` | 🟡 生产接线已落地，完整恢复与压缩待继续 |
| Phase 4 | 可重放会话 + token 感知压缩 | ⬜ 未启动 |
| Phase 5 | 提供商/扩展边界 + 能力注册表 | ⬜ 未启动 |
