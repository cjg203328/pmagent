# P2 模块边界

Status: current

P2 采用渐进式拆分：旧路径继续作为兼容 facade，新代码优先依赖无副作用职责模块。这样可以在不改变 Streamlit 首屏、SQLite 迁移和私有插件导入的前提下逐块迁移。

## UI

| 责任 | 当前模块 | 约束 |
| --- | --- | --- |
| 格式化与响应投影 | `artpm_agent/ui/formatters.py`、`ui/rendering.py` | 不导入 Streamlit、数据库或模型 |
| 知识意图与上下文投影 | `artpm_agent/ui/knowledge.py` | 只接收已读取的记录；查询仍由 Harness/store 负责 |
| 审批摘要 | `artpm_agent/ui/approvals.py` | 只处理已脱敏 payload，不执行批准动作 |
| 工作区/会话侧栏 | `artpm_agent/ui/workspace_selector.py`、`conversation_sidebar.py` | 页面状态由 Streamlit host 持有 |
| 旧入口 | `artpm_agent/ui_helpers.py` | 兼容导入和生命周期编排；逐步减少业务实现 |

## Agent 与 Provider

`runtime/agent_factory.py` 是 Agent 构造边界，`providers/port.py` 是模型端口，
`runtime/legacy_facade.py` 只记录旧 `chat/stream` 调用。`RequestOrchestrator`
保留兼容实现，不再拥有宿主 Store 生命周期或可选文档后端的顶层导入。

`providers/gateway_cache.py` owns bounded TTL/LRU client lifecycle and active
leases; `providers/contracts.py` owns provider-neutral response metadata.
`ModelGateway` remains the compatibility orchestration facade while retry,
selection and streaming responsibilities are migrated in later waves.

## API 与 Workflow

JSON normalization and SSE framing live in `api/serializers.py`; these are
pure transport helpers and are re-exported by `api/app.py` for compatibility.
System, capability, permission and workflow routes live under `api/routers/`;
route factories receive `GatewayServices` and helper ports instead of importing
process globals. `api/contracts.py` declares Store, EventBus and workflow
engine Protocols. The remaining workspace, search, chat, embed and voice routes
stay in `api/app.py` until their compatibility tests can move with them.

`workflows/store_schema.py` owns schema and migrations;
`workflows/store_codec.py` owns row/DTO serialization. `WorkflowStore` remains
the transactional facade, preserving its public API while database operations
are migrated by responsibility.

The API compatibility runner delegates its learning tail to the canonical
`harness.turn_service.complete_turn_lifecycle()` boundary. It must not maintain
independent reflection, episode or consolidation implementations.

## 类型治理

`runtime/`, `harness/`, `api/` and `tenancy/` are whole-package strict mypy
boundaries. `scripts/mypy_ratchet.py` invokes them with
`--strict --ignore-missing-imports --follow-imports=silent`; `Any` may remain
only at explicit compatibility or third-party adapter boundaries, not as a
service bundle field type.

## 样式

`artpm_agent/ui_style.py` 仍提供唯一的 `STYLE_CSS` 注入值。`ui/style_tokens.py`、`ui/style_layout.py` 和 `ui/style_components.py` 提供懒加载边界，避免出现多个 `<style>` 标签和 Streamlit 缓存抖动。后续迁移单个选择器时，只需替换对应 section 的实现，旧出口不变。

## 知识库

`memory/schema.py` 定义表和版本，`memory/knowledge_migrations.py` 承担迁移，`memory/knowledge_repository.py` 承担资源和摄取事务，`memory/knowledge_rule_service.py` 承担规则状态机，`memory/knowledge_search_service.py` 承担检索和向量同步，`memory/knowledge_projector.py` 以租约、退避和死信消费 durable outbox。`memory/workspace_knowledge_store.py` 仅组合这些职责并保留兼容入口。生命周期治理位于 `memory/lifecycle.py`。

## Chat 页面

`views/chat_state.py`、`chat_feedback.py`、`chat_welcome.py`、`chat_turn.py`、`chat_message_rendering.py` 和 `chat_execution.py` 分别保存状态投影、反馈控件、欢迎建议、排队状态、消息渲染与 canonical Harness 调用。`views/chat.py` 保留 Streamlit 页面生命周期和兼容私有函数。

## 迁移规则

1. 新纯函数先放入职责模块，再由旧路径 re-export。
2. 每次只迁移一个副作用边界，并保留原私有名称。
3. 页面模块不得把 SQLite 查询或模型调用复制到职责模块。
4. 完成两个版本的调用方迁移后，才删除 facade，并先更新 `CURRENT.md`、README 和导入契约测试。
5. 每个物理拆分都要补 AST/导入契约，防止职责静默回流到 facade。
