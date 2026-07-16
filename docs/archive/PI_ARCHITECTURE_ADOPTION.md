# pmagent 参考 Pi 的架构演进方案

## 参考基线

本方案基于 `earendil-works/pi` 的 `main` 分支
[提交 `0e6909f`](https://github.com/earendil-works/pi/tree/0e6909f050eeb15e8f6c05185511f3788357ddb3)
（2026-07-13）进行源码对照，
重点阅读以下边界：

- `packages/ai`：多供应商模型协议与消息转换。
- `packages/agent/src/agent-loop.ts`：低层 Agent Loop、工具执行和事件流。
- `packages/agent/src/harness`：会话、资源、压缩、Hook 和持久化编排。
- `packages/coding-agent`：产品层、扩展加载、工具和交互界面。

上游仓库：<https://github.com/earendil-works/pi>

## 结论

pmagent 不应照搬 Pi 的 TypeScript Monorepo 或终端 UI。真正值得复刻的是其依赖方向：

```text
UI / API
   |
Application Harness (session, profile, memory, workflow, approvals)
   |
Agent Loop (messages, turns, events, tool calls, abort, limits)
   |
Provider Adapters                 Tool Registry
   |                                  |
OpenAI / Anthropic / Custom       ArtPM Skills / MCP / Workflows
```

低层 Loop 不应知道 Streamlit、SQLAlchemy、报价规则或具体模型厂商。业务 Skill 不应直接控制
对话生命周期。UI 只消费事件，不应推断工具是否执行成功。

## 当前差距

当前 `ArtPMAgent` 超过 2,000 行，集中承担初始化、意图识别、附件处理、模型故障转移、
Skill 路由、上下文拼装、流式输出和结果格式化。主要风险是：

1. 新增模型、工具或界面时必须修改同一个高耦合类。
2. 文本流与工具执行没有统一的回合协议，难以可靠恢复、审计和重放。
3. Skill 元数据虽然已有风险和审批字段，但此前没有模型工具调用层的统一前置 Hook。
4. 会话记忆、业务数据库和运行时状态已分离，但缺少稳定的消息转换和上下文压缩边界。
5. `chat()` 兼具 Facade 和完整实现，测试只能大量依赖内部方法。
6. Wheel 仍发布 `agent`、`runtime`、`core` 等扁平模块；`artpm_agent.*` 尚未成为唯一安装 API。

## 本轮已落地

### 1. 稳定事件协议 ✅

`artpm_agent/runtime/events.py` 定义与 Pi 对齐的生命周期事件：

- `agent_start` / `agent_end`
- `turn_start` / `turn_end`
- `message_start` / `message_update` / `message_end`
- `tool_execution_start` / `tool_execution_update` / `tool_execution_end`
- `runtime_error`

事件携带 `run_id`、`turn_id`、消息快照和结构化工具信息，可由 Streamlit、日志、
持久化或未来 API 服务共同消费。

兼容层 `AgentRuntime` 已承接原有字符串流，并提供关键订阅者屏障、可选遥测订阅、
协作式中止、错误终态以及按 `workspace_id + conversation_id` 隔离的进程内消息快照。

### 2. Provider-neutral Agent Loop ✅

`artpm_agent/runtime/agent_loop.py` 新增独立低层 Loop：

- 多回合模型调用和工具结果回送。
- 每回合最大工具数和总回合硬上限。
- 协作式中止和重入保护。
- `context_transform`、`before_tool_call`、`after_tool_call`、
  `should_stop_after_turn` 扩展点。
- 工具异常规范化为 `toolResult`，交给下一模型回合解释，而不是击穿整个运行。
- 只在整批结果都声明 `terminate` 时提前结束，避免混合工具批次误停。

### 3. 工具注册表与安全适配 ✅

`artpm_agent/runtime/tools.py` 将现有 `SkillRouter` 映射为统一工具定义。模型只能看到名称、
描述和参数 Schema，看不到执行闭包与策略实现。

- 只读工具可以并行。
- 写入型工具强制顺序执行。
- `requires_approval` 工具默认阻断；所有 `read_only=False` 工具即使旧元数据漏标，也强制提升到宿主审批下限。
- 模型参数中的 `approved` 和 `confirmation_token` 会被移除。
- 只有宿主 `before_tool_call` 明确批准后，运行时才注入可信的 `approved=True`。
- 并行工具按实际完成顺序发事件，但按模型原始调用顺序写入会话。

`ArtPMAgent.create_agent_loop()` 提供低层接入口，`create_agent_session()` 负责 feature gate 和
append-only 持久化。`stream_events()` 已接入生产聊天链；默认配置
`agent_runtime.model_tool_calls_enabled=true`。运维可通过
`AGENT_MODEL_TOOL_CALLS_ENABLED=false` 紧急回滚；写入型工具仍需宿主显式审批。

### 4. 结构化 Provider Adapter ✅

`artpm_agent/providers/structured.py` 已实现 OpenAI-compatible 与 Anthropic Adapter：

- 直接解析供应商原生 tool-call 字段，返回 `AssistantTurn(content, tool_calls, metadata)`。
- 不从自然语言、Markdown 或 XML 样式文本猜测工具调用。
- 正确转换 assistant tool call 和 `toolResult` 历史，支持多回合工具结果回送。
- 保留实际模型、response ID、stop reason、usage 和故障转移信息。
- OpenAI 工具参数必须是合法 JSON object；非法 JSON 或非对象参数直接拒绝。

### 5. JSON Schema 参数边界 ✅

- `AgentTool` 注册时校验并冻结 Schema，执行前使用标准 `jsonschema` 校验完整参数。
- 校验错误给出稳定 JSON path，但不回显模型传入的敏感值。
- 18 个内置 Skill 与 5 个 Enhanced MCP 工具均发布非空对象 Schema。
- Schema 校验先于旧版 `Skill.validate` 和执行 callback，失败请求不会触发副作用。

### 6. Append-only 会话日志 ✅

`artpm_agent/memory/session_store.py` 在 `ConversationStore` 同一 SQLite 数据库中新增
`session_entries`：

- 每个 conversation 使用单调 `sequence`，全局使用自增 `id`。
- 同一 turn 可追加多个 assistant、tool call、tool result 和生命周期事件。
- `append_event()` 将 `AgentEvent` 结构化落库，`replay()` 按顺序回放。
- `AgentSession` 在事件向 UI/API 暴露前先完成持久化，持久化失败会终止本次运行。
- Store API 不提供 update/delete；旧 `messages` 表继续承担兼容聊天投影。

### 7. Phase 3 Stage 2：Profile 和 Knowledge Handlers ✅ 新增完成

Profile 提案和 Knowledge 摄取逻辑已从 `pages/chat.py` 迁移到独立 handler：

**新增模块**：
- `harness/profile_handler.py` (106 行) - Profile 配置变更提案检测
- `harness/knowledge_handler.py` (223 行) - Knowledge 摄取请求处理

**handler 功能**：
- `try_profile_proposal()` - 检测并创建 Profile 变更审批提案
- `try_knowledge_ingestion()` - 检测并创建知识库入库审批提案
- `is_knowledge_ingestion_request()` - 知识摄取请求模式匹配

**集成到 run_turn()**：
1. Profile handler（优先级最高）
2. Knowledge handler（次高优先级）
3. agent.chat() 回退（Stage 3 将添加更多 handler）

**测试覆盖**：新增 5 个单元测试（`tests/test_harness_handlers.py`），核心测试套件 33 passed。

**详细报告**：见 `PHASE3_STAGE2_REPORT.md`

### 8. 开发与交付门禁 ✅

- Pytest 源码路径不再依赖测试模块的导入顺序。
- 仅显式 `integration` 用例默认跳过，本地 MCP 安全回归会真实执行。
- Docker 在安装 `-e .`/项目包前先复制元数据与源码。
- CI 覆盖当前 `master` 分支，并使用用户命名空间的 Docker 镜像标签。

## 不照搬的部分

Pi 明确说明其核心不内置文件系统、进程、网络和凭据权限系统，而是依赖容器或沙箱。
pmagent 的业务动作包含发通知、写数据库和批量变更，因此必须保留并强化现有风险策略和
审批记录，不能因参考 Pi 而删除。

以下部分也不应直接复刻：

- Pi 的 TUI：pmagent 继续使用 Streamlit，并逐步让页面只订阅事件。
- Coding Agent 的 Bash/Edit 工具：仅在 pmagent 有明确业务场景和沙箱后引入。
- TypeScript 扩展加载方式：Python 侧应使用显式入口点、Allowlist 和签名/来源诊断。
- 完整会话树：当前先复用已有 SQLite 会话存储，再按需要引入分支与压缩条目。

## 后续路线

### Phase 2：结构化模型适配器（基础完成）

Provider Adapter、ModelGateway、JSON Schema 和契约测试已经完成。剩余工作是增加供应商原生
流式 tool-call 增量支持，以及真实 API 的显式 integration 测试；单元测试不会访问外部模型。

### Phase 3：Harness 与持久会话（进行中）

- `AgentSession` 已负责 Loop、Provider、SessionStore 和 feature gate 的组装。
- 所有生命周期事件已增量写入 append-only `session_entries`。
- 增加基于 token 预算的 `context_transform`，优先裁剪工具详情，再做摘要。
- 下一步将审批决定和压缩记录也作为 typed entry 保存，并实现重启恢复。

完成标准：进程重启后可以从最后一个完整事件继续，不重复执行已完成的写操作。

### Phase 4：资源加载与信任边界

- 统一加载内置 Skill、MCP Skill、Workspace 配置和 Agent Profile。
- 加载结果同时返回 diagnostics，不用 `print()` 静默吞掉冲突。
- 动态资源默认不可信；只有显式 Allowlist 才能成为自动执行的只读工具。
- 热重载生成新的不可变工具快照，运行中的回合继续使用旧快照。

完成标准：重复名称、无效 Schema、未知来源和权限升级都能在启动时被诊断。

### Phase 5：缩小 `ArtPMAgent`

按以下顺序迁移，避免大爆炸式重写：

1. 模型故障转移 -> `providers/gateway.py`。
2. 附件解析与视觉临时文件 -> `attachments/service.py`。
3. 意图规则与输入提取 -> `routing/service.py`。
4. Skill 结果格式化 -> `presentation/skill_results.py`。
5. `ArtPMAgent` 最终只保留兼容 Facade 和依赖组装。

完成标准：`ArtPMAgent` 不包含供应商重试、文件解析细节和大段业务格式化分支。

## 开发规则

后续改造应保持以下约束：

1. 每个 Phase 都先加契约测试，再切换调用方。
2. 新旧路径短期双轨，但同一请求只能执行一个路径。
3. 写操作必须携带幂等键；重试模型请求不得隐式重试已开始的写工具。
4. 事件是 UI 和持久化的唯一运行态来源，日志不是业务状态。
5. 工具失败应抛异常，由 Loop 生成错误结果；不要把失败伪装为成功文本。
6. 上下文压缩不能删除未配对的工具调用与结果，也不能删除待审批状态。
7. 供应商能力差异留在 Adapter，不能渗入业务 Skill。
