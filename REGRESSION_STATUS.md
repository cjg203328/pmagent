# 架构层再优化 — 回归确认状态

## 背景
用户要求基于架构层重新优化 AI 部署 / 模型识别 / Agent 调优与性能，并覆盖
「学习 · 进化 · 输出 · 处理 · 解决问题」五目标闭环。前序会话已交付 4 个增量模块 +
集成点 + `ARCHITECTURE_OPTIMIZATION.md` 设计文档。

## 本轮收尾动作：全量回归确认
1. 隔离复现 `test_inject_respects_budget_end_to_end`（此前 `.pytest-full6` 报 1 失败，
   `ctx.knowledge_context` 为空）。
2. 逻辑推演证明：在**当前代码 + 当前测试**下，该场景的反馈块（优先级 3，最高）**不可能**
   被预算逻辑丢弃 → 上下文不可能为空。故原失败为**会话中途重写测试文件产生的陈旧 `.pyc`**
   （pytest 运行了改写前的字节码）。
3. 全仓清除 `__pycache__` 与 `*.pyc`，重建干净字节码。

## 验证结果（全部绿灯）
- 24 个新增用例（5 个测试文件）：全绿
  - `test_model_registry`(6) / `test_response_cache`(5) / `test_telemetry`(3) /
    `test_gateway_optimization`(5) / `test_memory_retrieval_budget`(5)
- 针对性排序复现（monkeypatch 文件 → 记忆注入 → 预算测试）：14 全绿
- **全量回归 `.pytest-full7.log`：`664 passed, 2 skipped in 98.17s`，零失败**

## 交付物（模块均为纯增量、可插拔、默认不改既有行为）
| 模块 | 作用 | 启用开关 |
|---|---|---|
| `providers/model_registry.py` | 任务分层选模型（routing→廉价同族 / reasoning→最强 / vision→须有视觉 / chat→均衡） | 默认开 |
| `providers/response_cache.py` | 精确键 + TTL + 有界 LRU 响应缓存，避免重复 API 调用 | `ARTPM_RESPONSE_CACHE=true` |
| `runtime/telemetry.py` | 每次调用记延迟/回退/缓存命中/成本 → `data/telemetry.db`，供复盘挖性能回归 | `ARTPM_TELEMETRY=false` 关（默认开） |
| 上下文预算 + 路由 LRU 缓存 | `inject_memory_context(max_context_chars)` 封顶；`IntentRouter.detect` 内存缓存（上限 256） | 默认开 |

## 五目标闭环落地映射
- **处理**（Processing）：`IntentRouter` 三级意图 + LRU 路由缓存
- **学习**（Learning）：`inject_memory_context` 记忆激活 + 上下文预算
- **输出**（Output）：`ModelGateway.best_model_for_task` 任务分层选模型 + 响应缓存
- **解决问题**（Problem-solving）：`run_turn` / skills / 模型回退 + 遥测
- **进化**（Evolution）：`ReflectionJob` / `StrategyStore` / `MetaMemory` 复盘反哺

## 结论
架构层再优化四项增量模块与集成点**无回归**，全量测试绿灯。五目标闭环已落地并验证。
