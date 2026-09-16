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

## 样式

`artpm_agent/ui_style.py` 仍提供唯一的 `STYLE_CSS` 注入值。`ui/style_tokens.py`、`ui/style_layout.py` 和 `ui/style_components.py` 提供懒加载边界，避免出现多个 `<style>` 标签和 Streamlit 缓存抖动。后续迁移单个选择器时，只需替换对应 section 的实现，旧出口不变。

## 知识库

`memory/schema.py` 定义表和版本，`memory/resources.py` 定义文本/JSON/limit 校验，`memory/rules.py` 定义规则状态，`memory/serializers.py` 定义 DTO 投影，`memory/vector_index.py` 只包含可重建的分块逻辑，`memory/knowledge_projector.py` 消费 durable outbox。`memory/workspace_knowledge_store.py` 仍是事务 facade，外部代码不应读取 SQLite 行结构或直接写向量索引。

## Chat 页面

`views/chat_state.py`、`chat_feedback.py`、`chat_welcome.py` 和 `chat_turn.py` 只保存状态投影、反馈判断、欢迎建议和回合 ID 等纯边界。`views/chat.py` 保留 Streamlit 生命周期、事件订阅和兼容私有函数；回合执行继续由 Harness adapter 负责。

## 迁移规则

1. 新纯函数先放入职责模块，再由旧路径 re-export。
2. 每次只迁移一个副作用边界，并保留原私有名称。
3. 页面模块不得把 SQLite 查询或模型调用复制到职责模块。
4. 完成两个版本的调用方迁移后，才删除 facade，并先更新 `CURRENT.md`、README 和导入契约测试。
