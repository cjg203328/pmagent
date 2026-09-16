# ArtPM Agent 执行映射

Current runtime host contract (current):

```text
API / Streamlit / CLI
        -> LocalHarnessRuntime (request scope + services)
        -> TurnContext
        -> run_turn() exactly once
        -> TurnResult + lifecycle events
```

`runtime_counters` is a process-local diagnostic snapshot returned by the API
health surface. It includes canonical/legacy turn counts and attachment parser
attempt, reuse and failure counts. A parser snapshot is valid only for the
current `TurnContext`; callers must not pre-seed empty `parsed_files` or
`attachment_context` values to claim that parsing already happened.

本文是当前代码的运行时地图。它解决两个问题：一次请求从哪里进入，以及每类状态应该由哪个模块负责。新功能先挂到这张地图，再决定是否需要新增文件。

## 1. 唯一主链

```text
Streamlit / REST / CLI
        |
        v
TurnContext + TurnServiceBundle
        |
        v
harness.turn_service.run_turn()
        |
        +-- fast meta response (仅问候、身份、能力、模型、记忆能力)
        |
        +-- memory injection (一次，幂等)
        |     +-- conversation summary
        |     +-- MemoryManager / TencentDB
        |     +-- WorkspaceKnowledgeStore + accepted rules
        |     +-- feedback / strategy
        |     +-- token budget
        |
        +-- workspace retrieval contract
              +-- trusted SearchTarget
              +-- RetrievalPlan
              +-- bounded WorkspaceRetriever
              +-- RetrievalHit / citation metadata
        |
        +-- profile proposal
        +-- knowledge ingestion proposal
        +-- knowledge rule proposal
        +-- artifact generation
        +-- persisted workflow routing
        +-- intent -> skill -> approval -> execution
        +-- model fallback / OCR degradation
        |
        v
TurnResult -> UI/API renderer -> conversation message
```

`run_turn()` 是 API 和 UI 的规范边界。`ArtPMAgent.chat()`、`stream_chat()` 和 `RequestOrchestrator.stream_events()` 只保留兼容用途，新业务逻辑不得继续放回这些 facade。

## 2. 入口与文件映射

| 责任 | 唯一主文件 | 说明 |
| --- | --- | --- |
| Streamlit 入口 | `artpm_agent/app.py`, `artpm_agent/views/chat.py` | 只负责页面、交互和渲染 |
| REST 入口 | `artpm_agent/api/app.py`, `artpm_agent/api/services.py` | 负责身份、workspace、会话消息和 API 错误契约 |
| 回合编排 | `artpm_agent/harness/turn_service.py` | 统一 handler 顺序和 TurnResult |
| 回合上下文 | `TurnContext`, `TurnScope` | 用户输入、历史、附件、租户范围和回合状态 |
| 回合依赖 | `artpm_agent/runtime/request_services.py` | `TurnServiceBundle` 注入知识、工作流、档案和记录服务 |
| Provider 适配 | `artpm_agent/harness/runtime.py` | `HarnessRuntime` 和旧 Agent 的唯一适配层 |
| 意图识别 | `artpm_agent/routing/service.py` | keyword -> embedding -> optional LLM classifier |
| 工作区检索 | `artpm_agent/retrieval/` | trusted scope -> RetrievalPlan -> WorkspaceKnowledgeStore -> RetrievalHit |
| Skill 安全执行 | `artpm_agent/harness/skill_handler.py` | 意图缓存、租户绑定、权限审批、执行和结果格式化 |
| 模型回退 | `artpm_agent/harness/model_handler.py` | prompt、视觉附件、failover 和 OCR 降级 |
| 附件处理 | `artpm_agent/harness/attachment_pipeline.py` | 一次规范化快照，供知识、产物和模型 handler 复用 |
| 结构化工具循环 | `artpm_agent/runtime/agent_loop.py`, `artpm_agent/harness/agent_session.py` | 受配置开关控制，工具 schema、审批和 SessionStore 持久化 |
| 事件与运行记录 | `artpm_agent/runtime/events.py`, `turn_events.py`, `event_bus.py` | 生命周期、工具审计、会话回放和订阅 |
| 会话消息 | `artpm_agent/memory/conversation_store.py` | 用户可见的 user/assistant transcript |
| 回合/工具事件 | `artpm_agent/memory/session_store.py` | append-only 运行事实，不替代消息表 |
| 性能遥测 | `artpm_agent/runtime/telemetry.py`, `providers/gateway.py` | 延迟、模型、token、fallback、连接状态 |

## 3. TurnContext 边界

### 稳定字段

- `turn_id`, `conversation_id`, `user_input`：回合标识和原始请求。
- `conversation_history`：已排除当前 user message 的有限历史。
- `attachments`：宿主校验后的附件元数据。
- `scope`：tenant、workspace、actor 和角色的可信范围。
- `runtime`：只暴露 `HarnessRuntime`，不要在 handler 中访问 `ArtPMAgent` 私有方法。
- `services`：当前请求的 `TurnServiceBundle`。
- `intent`, `intent_checked`, `memory_injected`：回合内幂等状态。

### 兼容字段

`extra` 仍保留给旧插件和宿主。新代码应优先读取显式字段；新增状态只有在确有旧接口需要时才同步到 `extra`。不得把数据库连接、完整配置或未脱敏凭据塞入发送给模型的 context。

## 4. 记忆生命周期

```text
TurnContext
  -> memory_injected guard
  -> conversation compression
  -> scoped long-term memory
  -> scoped workspace resources/rules
  -> feedback / strategy
  -> bounded knowledge_context
  -> model / skill / workflow
```

- Workspace 资料和规则必须带 `workspace_id`；租户上下文由宿主创建，不能由模型输入覆盖。
- 规则、资料入库先生成 proposal，确认后才进入 active/accepted 集合。
- 记忆召回失败只降级为空上下文并记录诊断，不应阻塞主请求。
- `MemoryManager` 负责通用长期记忆；`WorkspaceKnowledgeStore` 负责工作区资料和规则；`ConversationStore` 负责会话消息。不要在入口层再次手工拼接知识上下文。

## 5. 意图与工具生命周期

```text
intent_checked = false
        |
        v
IntentRouter.detect()
        |
        v
skill_handler: tenant bind -> metadata floor -> approval gate
        |
        +-- pending approval: persist request, do not execute
        +-- approved/allowed: tool_execution_start -> execute -> tool_execution_end
        +-- no match/failure: model fallback
```

同一回合只允许一次意图检测。`stream_chat()` 的旧兼容流在 Harness 管理回合中只能输出模型增量，不能再次执行 Skill。结构化模型工具调用必须经过 `AgentLoop` 的 JSON Schema 和 `AgentSession` 的事件持久化。

## 6. 记录分类

| 记录 | 存储 | 是否用户可见 | 规则 |
| --- | --- | --- | --- |
| 应用日志 | `logs/*.log` | 否 | 诊断、异常堆栈、无敏感值 |
| 回合事件 | `SessionStore` / EventBus | 否 | `turn_start/end`，按顺序追加 |
| 工具审计 | `SessionStore` | 否 | 工具名、受限参数摘要、结果状态、审批边界 |
| 会话消息 | `ConversationStore` | 是 | user/assistant transcript，稳定错误码 |
| 性能遥测 | `telemetry.db` | 否 | 延迟、provider、模型、token、fallback |

日志、遥测和回合记录均不得把 provider 密钥、完整 `.env` 或未脱敏附件正文写入诊断输出。

## 7. 兼容迁移规则

1. 新入口先构造 `TurnContext` 和 `TurnServiceBundle`，再调用 `run_turn()`。
2. 新 handler 只能依赖 `HarnessRuntime` 和显式服务，不得回调 `RequestOrchestrator.chat()`。
3. 旧 facade 只做适配和向后兼容；每次触碰旧路径都要补迁移测试。
4. 新的 durable 运行事实进入 `SessionStore`，不要在 UI 里自行写 JSON 或另建一套 event schema。
5. API/UI 的行为差异必须通过服务包或 scope 明确表达，不能通过入口侧额外检索或 fallback 隐式产生。
