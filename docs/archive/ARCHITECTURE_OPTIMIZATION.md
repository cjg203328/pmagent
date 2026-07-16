# ArtPM Agent — AI 部署 / 模型识别 / Agent 调优与性能优化（架构层）

> 生成时间：2026-07-16。基于 `ARCHITECTURE_SUMMARY.md` 与 `MEMORY_EVOLUTION_DESIGN.md` 的架构层再优化。
> 原则：**纯增量、可插拔、默认不改变既有行为**。所有新能力均通过环境变量 / 配置开关启用，关掉即回到原行为。

## 1. 现状诊断（对照五个目标）

| 目标 | 现状 | 主要瓶颈 |
|---|---|---|
| 处理 / 识别 | `IntentRouter` 三层（关键词→embedding→LLM），embedding 为离线哈希向量、LLM 层默认关闭 | 语义识别弱、每次请求都重算、无缓存 |
| 输出 / 部署 | `ModelGateway` 单主模型 + 平铺回退 | 无"按任务分层选模型"、无响应缓存、无性能遥测 |
| 学习 | 记忆注入 + 知识炼化 + 反馈（Phase 1/2） | 注入上下文无 token 预算，长对话撑大成本/延迟 |
| 进化 | 复盘→策略库→注入（Phase 3/4） | 性能信号未反哺复盘 |
| 解决问题 | 技能 + 工作流 + 审批护栏 | 已具备，无需改动 |

## 2. 五目标闭环架构（This Agent 的"生命循环"）

```
                ┌─────────────────────────────────────────────────────────┐
   用户输入 ──▶ │  ① 处理 (Processing)                                       │
                │     IntentRouter(关键词→embedding→LLM) + 路由决策缓存      │
                │            │                                               │
                │            ▼                                               │
                │  ② 学习 (Learning)                                         │
                │     inject_memory_context: 长期记忆 + 偏好 + 策略 + 元记忆  │
                │     └─ MemoryContextBudget / max_context_chars 封顶        │
                │            │                                               │
                │            ▼                                               │
                │  ③ 输出 (Output)                                           │
                │     ModelGateway.best_model_for_task(task_type)            │
                │       单层/分层选模型 → 响应缓存 → 性能遥测                 │
                │            │                                               │
                │            ▼                                               │
                │  ④ 解决问题 (Problem Solving)                              │
                │     run_turn(): 技能/工作流编排 + 审批护栏 + 工具执行       │
                │            │                                               │
                │            ▼                                               │
                │  ⑤ 进化 (Evolution)                                        │
                │     record_outcome → Episode → ReflectionJob → 策略库       │
                │       遥测(summarize) 反哺复盘；策略下一回合注入 ②          │
                └─────────────────────────────────────────────────────────┘
```

五个阶段首尾相接：**进化产出的策略回到"学习"阶段注入**，性能遥测回到"进化"阶段被复盘挖掘——形成自我改进的闭环。

## 3. 本次新增的 4 个增量模块

### 3.1 `providers/model_registry.py` — 模型能力注册表 + 任务分层选模型（识别 + 部署）
- `ModelCapabilityRegistry`：从 `llm` 配置构建每个模型的 `vision / tier(cheap|general|strong) / cost / latency` 能力画像。
- `ModelGateway.best_model_for_task(task_type, require_vision)`：
  - `routing`（轻量分类）→ 同家族内最廉价模型（如 `gpt-4o-mini`）；
  - `reasoning`（重推理）→ 最强模型（如 `o3`），**不受家族偏好限制**；
  - `vision` → 必须有视觉能力的模型；
  - `chat` → 同家族内均衡模型，不降级到廉价模型。
- 接入点：`harness/model_handler.py` 主聊天路径已传 `task_type="chat"`，零行为变化（默认仍走主模型）。

### 3.2 `providers/response_cache.py` — LLM 响应缓存（性能）
- 精确键 `(model, system, prompt, history, images)`，带 TTL 与有界 LRU。
- 接入 `ModelGateway.chat_with_failover / stream_with_failover`，**best-effort**（缓存层任何异常被吞掉，绝不阻断真实回复）。
- **默认关闭**。开启：`ARTPM_RESPONSE_CACHE=true`，可选 `ARTPM_RESPONSE_CACHE_TTL=3600`。

### 3.3 `runtime/telemetry.py` — 回合遥测（性能优化 + 反哺进化）
- `AgentTelemetry`：每次模型调用记录 `task_type / model_used / latency_ms / fallback / cache_hit / success / tokens_est`，落库 `data/telemetry.db`（独立库，不碰业务表）。
- `summarize()` 给出 `avg_latency / p95 / fallback_rate / cache_hit_rate / failure_rate`，供 `ReflectionJob` 挖掘性能回归。
- **默认开启**（轻量、只追加写）。关闭：`ARTPM_TELEMETRY=false`。

### 3.4 上下文预算 + 路由缓存（处理 / 性能）
- `inject_memory_context(max_context_chars=...)`：超限时按优先级丢弃最低优先级块（元记忆→策略→偏好），仅剩最高优先级（记忆）时就地截断。**默认 0 = 不封顶**。
- `IntentRouter.detect`：新增内存 LRU 路由缓存（上限 256），相同/近似输入直接命中，**确定性、始终开启、零行为变化**。

## 4. 启用建议（部署侧）

| 能力 | 开关 | 建议 |
|---|---|---|
| 响应缓存 | `ARTPM_RESPONSE_CACHE=true` | 生产环境强烈建议开启，重复问题直接命中，省成本降延迟 |
| 遥测 | 默认开（设 `ARTPM_TELEMETRY=false` 关） | 保留，复盘可据此优化 |
| 任务分层选模型 | 配置 `available_models` 含 cheap/strong 变体 | 配上 `mini`/`o3` 之类即可自动分层 |
| 上下文预算 | `execute_turn_with_harness(memory_inject_token_budget=4000)` | 长对话场景开启，控成本 |
| 路由缓存 | 已默认开 | 无需操作 |

## 5. 测试与回归
- 新增 24 个测试（`test_model_registry` / `test_response_cache` / `test_telemetry` / `test_gateway_optimization` / `test_memory_retrieval_budget`）全绿。
- 全量回归（`.pytest-full6.log`）确认既有 636 用例无破坏。

## 6. 未来方向落地状态（已全部完成 ✅）

> 更新时间：2026-07-14。下列三项原为 §6 的"仍可继续方向"，现已实现并接入，全部保持**纯增量 / 可插拔 / 默认不改变既有行为**。

### 6.1 `task_type="routing"` 接入 LLM 分类器 → 分层模型 ✅
- 新增 `artpm_agent/routing/task_classifier.py`：`LLMTaskClassifier`（LRU 缓存 256）。
  - `classify(user_input, image_paths=None) -> str` 输出 `chat` / `reasoning` / `vision` / `routing`。
  - 关闭时直接返回 `"chat"`；LLM 返回非法标签时回退到启发式（有图→vision；含"为什么/分析/算"等→reasoning；否则 chat）。
- `ModelGateway` 扩展：`_read_task_classifier_flag()` / `_get_task_classifier()` / `classify_task()`。
  - `chat_with_failover` / `stream_with_failover` 在 `task_type is None` 且**分类器已启用**时自动分类，再经 `best_model_for_task` 选分层模型。
  - **关键约束**：分类器默认关闭（`_task_classification_enabled=False`）时，`task_type` 保持 `None`，**不触发** `best_model_for_task` 重排序，完全保留原 failover 顺序（主模型优先 → 回退）。这是避免回归破坏的核心点。
- 启用开关：`ARTPM_TASK_CLASSIFIER=1|true|yes|on` 或 `llm_config["task_classifier_enabled"]=True`。
- 测试：`tests/test_task_classifier.py`（12 项，覆盖关闭/启发式/LLM 标签归一化与回退/gateway 分层选模型）。

### 6.2 遥测运营看板 ✅
- `runtime/telemetry.py` 新增聚合：`by_task_type(window)` / `by_model(window)` / `latency_trend(window)`（时间升序）。
- 新增 `runtime/telemetry_dashboard.py`：
  - `collect_dashboard(telemetry, window=500)` 产出结构化看板数据（总量/latency 中位数·P95/任务分布·模型分布/趋势 sparkline）。
  - `render_html(dashboard)` 输出自包含浅色主题 HTML（卡片 + SVG sparkline + 分布表）。
  - CLI：`python -m artpm_agent.runtime.telemetry_dashboard --db data/telemetry.db --out report.html --window 500`。
- 新增 `telemetry_dashboard_app.py`：独立 Streamlit 入口（`streamlit run telemetry_dashboard_app.py`，`@st.cache_data(ttl=30)`），只读、增量，不改动 `app.py` 路由。
- 测试：`tests/test_telemetry_dashboard.py`（7 项）。

### 6.3 `MemoryContextBudget` 的 token 压缩扩展（Pi Phase 4）✅
- 新增 `harness/token_budget.py`：
  - `estimate_tokens(text, *, model, counter)`：优先 `counter` → tiktoken（若装且给 model）→ CJK 启发式（CJK≈1.6 字符/token，Latin≈4 字符/token）。
  - `compact_text(text, *, keep_ratio=0.85)`：去重行/折叠空白的"无损→轻损"清理；**安全边界以原始长度为基准**，短文本清理干净后绝不被截断。
  - `apply_token_budget(blocks, max_tokens, *, counter, model, compression, priority_fn, summarize_fn)`：先丢最低优先级整块，再对剩余顶部块 compact / 调用 `summarize_fn` / token 前缀截断（二分），与原字符预算同规则。
- `harness/memory_retrieval.py`：`MemoryContextBudget` 新增 `max_total_tokens / compression / token_model / token_counter / summarize_fn`；`inject_memory_context` 新增 `max_context_tokens`，仅当 `>0` 时走 token 路径，否则走原字符路径。
- `internal/chat_harness_integration.py`：新增 `memory_inject_max_tokens` 透传参数。
- **默认值 `0`（关闭） ⇒ 行为完全不变**。
- 测试：`tests/test_token_budget.py`（13 项）。

### 6.4 回归说明
- 修复一处**既有**无关缺陷：`artpm_agent/artifacts/coordinator.py` 的 `_generate_from_template` 在 LLM 调用（如离线）抛 `RuntimeError` 时未捕获，导致"离线从模板生成"路径中断；已在 LLM 调用 `except` 中补 `RuntimeError`（与测试 `..._from_it_offline` 意图一致）。该文件不在本次三个特性范围内，属顺手修掉的预存 bug。
- 全量回归（2026-07-14）：**696 passed, 2 skipped**（2 个 skip 为 `ART_ENABLE_INTEGRATION=1` 门控的集成测试）。清 `.pyc` 后复跑稳定绿。

