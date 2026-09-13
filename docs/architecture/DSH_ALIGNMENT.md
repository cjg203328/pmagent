# DeepSeek Harness（dsh）对齐改造

> 更新时间：2026-08
> 参考：[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)

> **项目边界**：本文仅记录架构思想在 Python 中的独立实现。ArtPM Agent 不导入、
> 启动或修改本机 DeepSeek Harness，也不共享其插件、配置、会话、工作区和数据目录。
> 两个项目可以分别安装、升级、启动和卸载。

本项目的优化开发借鉴了 DeepSeek 官方 agent harness 的设计。dsh 是 Node.js/Cordis
项目，本仓库是 Python 项目，因此采用「移植设计理念」而非「替换运行时」的方式：
把 dsh 的插件化能力注册、三域事件、配置分层、工具把关流水线、subagent、plan、
session-query 等概念，以 Python 惯用法落地到现有代码库。

## 一、DeepSeek 官方 Provider

配置 `LLM_PROVIDER=deepseek` 即可接入 DeepSeek 官方 API（`deepseek-chat` /
`deepseek-reasoner`），新增 `DEEPSEEK_API_KEY`、`DEEPSEEK_API_BASE`（默认
`https://api.deepseek.com`）、`DEEPSEEK_REASONING_EFFORT`（`off` / `high` / `max`）。

实现对齐 dsh `llm-deepseek` 适配器的 wire 规则：

- **思维链单独捕获**：`reasoning_content` 从普通文本分离，非流式与流式均存入
  `client.last_reasoning_content`，供 UI 展示；绝不混入返回文本。
- **reasoning_effort 三档**：`off` 序列化为 `thinking: {type: "disabled"}`；
  `high`/`max` 透传为顶层 `reasoning_effort`；空值保持 provider 默认。
- **content 永不置 null**：纯工具调用或纯思考回合返回 `""`，避免破坏持久会话。
- **多模态明确报错**：DeepSeek 官方 chat-completions 目前不支持图片，明确抛出
  `当前模型客户端不支持图片`，让网关能力识别接管。

DeepSeek 同时接入：故障转移白名单、结构化输出适配（OpenAI 兼容路径）、
`check_config` 校验、设置页（provider 下拉 / key / base / effort）、模型同步。

## 一·二、视觉模型搭配（"眼睛模型"）

主模型（如 DeepSeek）不支持图片时，图片请求自动路由到独立的视觉模型，
二者可来自不同供应商。配置：

```env
LLM_VISION_PROVIDER=zhipu
LLM_VISION_MODEL=glm-4v-flash
ZHIPU_API_KEY=…            # 或 LLM_VISION_API_KEY 单独指定视觉 key
```

- `ModelGateway` 新增 `_chat_with_vision` / `_stream_with_vision`：图片请求
  绕过文本主模型/故障转移路径，直接走视觉 client（惰性构建一次）。
- 视觉 client 配置自动组装：provider 对应 key/base（`zhipu` 默认
  `https://open.bigmodel.cn/api/paas/v4`），`max_tokens` 上限 1024
  （GLM-4V-Flash 固定输出上限），不参与跨 provider 故障转移。
- 未配置 `LLM_VISION_MODEL` 时行为不变（图片仍走原路径）。
- 视觉请求同样接入响应缓存与遥测。

## 二、插件系统扩展：从「skill 插件」到「能力插件」

原插件系统只允许注册 `BaseSkill` 子类。现扩展为四类能力，manifest 支持
`tools` / `handlers` / `providers` 声明（与 `skills` 并列，至少声明一类）：

```json
{
  "plugin_id": "my.plugin",
  "version": "1.0.0",
  "module": "plugin.py",
  "module_sha256": "…",
  "tools": [{ "name": "echo_tool", "class": "EchoTool", "read_only": true, "requires_approval": false }],
  "providers": [{ "name": "alt", "class": "build_alt_client", "read_only": true, "requires_approval": false }]
}
```

- 安全模型不变：SHA-256 校验 + ID allowlist + 隔离加载。
- 新增 `CapabilityRegistry`（`plugins/capabilities.py`）：统一注册表，聚合四类能力，
  支持 `merge_plugin_report(report)` 批量合并、按名查询、线程安全去重。
- `create_llm_client` 新增扩展点：未识别的 provider 名会查询能力注册表，
  部署可注册自定义 provider 工厂而无需 fork 代码。
- `build_capability_registry` 可选 `plugin_registry` 参数：插件工具工厂（返回
  `AgentTool`）自动合并进运行时工具注册表（内置工具优先）。

## 三、三域事件总线

`AgentEvent` 新增 `domain` 字段（默认 `agent`，向后兼容）：

| 域 | 含义 | 用途 |
|---|---|---|
| `session` | 持久事实 | 追加 JSONL 会话日志，进程重启后可回放 |
| `agent` | 运行中观测 | agent loop 事件流（既有生成器模式不变） |
| `capability` | seam 通知 | 工具/fs/遥测策略挂载，避免循环导入 |

- `EventBus`（`runtime/event_bus.py`）：`subscribe(handler, domain=, event_type=, filter=)`
  返回幂等退订函数；`publish` 保证订阅者异常隔离（单个失败不影响其他订阅者，
  记录在 `DeliveryReport.failures`）；`attach_sink` 挂持久化 sink。
- `SessionEventLog`：JSONL 追加日志 + `replay(domain/event_type/run_id/limit)`，
  容忍坏行（部分写入不会损坏整个日志）。
- `build_bus_with_session_log(path)` 一键组装「bus + session 域自动落盘」。

## 四、配置分层（profile + patch）

dsh 的 profile/bundle 分层模型，Python 化落地（`config_layers.py`）：

- `apply_patch(base, patch)`：patch 定位键并**替换整个值**（不做深度合并），
  支持点路径（`llm.model`），深拷贝防泄漏。
- 层序（后层胜出）：`base(default_config.json)` → profile patch →
  home patch（`~/.artpm/config.patch.json`，可选）→ `--patch` overlays →
  **环境变量（最高优先级，部署层权威不变）**。
- `Config` 在 env 覆盖前应用 `ARTPM_PROFILE` 指定的 profile。
- CLI：`python -m artpm_agent.config_layers --profile dev --dump-config` 输出
  合并配置并标注每键来源层（`--list-profiles` 列出可用 profile）。

## 五、工具把关流水线与结果溢写

`runtime/pipeline.py` 对齐 dsh 的 `pre-execute → execute → post-execute`：

- `ToolExecutionPipeline`：有序 pre 策略（首个返回 `BeforeToolCallDecision` 即短路）
  + post 策略（按序链式改写结果）。`AgentLoop` 新增可选 `pipeline` 参数，
  与既有单 hook 组合（pipeline 策略在前，遗留 hook 在后）；不传时行为不变。
- `spill_policy` / `spill_tool_result`：超长工具结果截断为有界摘要，全文落盘到
  spill 目录，`details.spilled_path` 指向完整文件（默认 16k 字符上限，
  `load_spilled_result` 可回读）。

## 六、subagent 子代理委派

`runtime/subagent.py`：

- `SubagentRequest` / `SubagentResult`：委派任务的输入输出契约。
- `SubagentExecutor` 抽象 + `InProcessSubagentExecutor`（宿主回调适配），
  与具体模型/运行时解耦。
- `SubagentPool` 护栏：并发信号量（默认 4）、嵌套深度上限（默认 3）、
  单任务超时（默认 120s）；深度越限/超时/异常均转为错误结果而非抛进主循环。
- `subagent_delegate_tool(pool)`：模型可见的 `subagent_delegate` 工具，
  主代理可把自包含子任务交给独立子代理并取回结论。

## 七、plan 计划协作（审批式执行）

`runtime/plan.py` 对齐 dsh 的 plan 模式：

- 状态机：`draft → proposed → approved → executed`；`reject(反馈)` 退回 draft，
  `revise` 按反馈修订步骤。
- `PlanStore`：JSON 原子持久化（临时文件 + rename），线程安全。
- `confirm_steps`：高危步骤（`requires_approval=true`）执行前需宿主显式确认，
  未确认的步骤标记 `blocked` 绝不执行——与项目「写入需审批」规则一致。
- `execute(plan_id, runner)`：仅 approved 可执行；runner 异常标记步骤 failed
  并停止后续步骤，保留部分执行状态。

## 八、会话查询（session-query）

`runtime/session_query.py` 在持久会话日志上提供只读查询面：

- `search(text)`：跨全部字符串字段（含嵌套 `message.content`）的大小写不敏感
  全文检索，可限长取最新。
- `lineage(run_id)`：按 turn 分组重建一次运行的完整事件链（`RunLineage`）。
- `summary(run_id)`：消息数 / 工具调用数 / 错误数 / 时间跨度统计。
- `bounded_read(run_id, max_events, max_chars)`：事件数与字符数双重有界读取。

## 九、验证

- 新增测试文件：`test_deepseek_client.py`、`test_plugin_capabilities.py`、
  `test_event_bus.py`、`test_config_layers.py`、`test_tool_pipeline.py`、
  `test_subagent.py`、`test_plan.py`、`test_session_query.py`。
- 全量回归：`python -m pytest -q`（不开启覆盖率时用 `--no-cov`）。
- 代码质量：`ruff check artpm_agent tests`。

## 十、后续建议（未在本轮实现）

- 把 `EventBus.forward` 接入 `AgentRuntime` 事件流，使每个 agent run 的
  session 域事件自动落盘。
- `plan_submit` / `subagent_delegate` 等模型可见工具接入 UI 工作流设计器。
- spill 目录纳入 `data/` 管理并支持清理策略。
- 会话日志提供 SQLite FTS5 全文索引（当前为子串检索）。
