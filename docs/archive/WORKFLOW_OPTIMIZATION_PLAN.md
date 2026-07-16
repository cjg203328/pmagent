# ArtPM Agent 工作流优化路线图

> 面向**美术项目经理**日常工作流的 7 大方向优化。基于代码实测现状（非臆测）制定，避免重复造轮子。

## 现状速览（探查结论）
- **已有强能力**：需求评估（文档解析 + `project_evaluator`）、成本核算（`QuoteCalculator`）、报价排期（`TaskAllocator`）、进度管理（`ProgressTracker` + 催办）、复盘数据底座（`DataAnalyzer`/`TrendAnalyzer`）。
- **最大空白**：质量把控（零技能、零路由，仅 `Task.quality_score`/`revision_count` 两字段）。
- **脱节缺口**（各方向共有）：`config.staff_levels.daily_cost` 未用于报价；`Asset.complexity` 未被任何逻辑读取；`progress_tracking.check_interval_hours` 无调度消费；文档解析结果不自动落库。

## 任务清单与状态

| # | 方向 | 状态 | 本次交付 / 下一步 |
|---|------|------|------|
| 1 | 质量把控 | ✅ 已完成(becc738) | `quality_control` 技能：提交评审 → 通过/驳回/返工状态机 + 质量评分 + 质检报告 |
| 2 | 产品交付 | ✅ 已完成(fee24d4) | `Delivery`/`AssetVersion` 模型 + 交付清单/验收单/交付与版本记录技能 |
| 3 | 复盘总结 | ✅ 已完成(本轮) | `retrospective` 技能：结项报告（时间线/成本偏差/质量均分/准时率）+ 经验自动沉淀 KnowledgeBase |
| 4 | 需求评估 | ✅ 已完成(aa88e9a) | `Asset.complexity` 真实参与评估；报价解析自动建 Project+Assets（修 intake→落库断层）；范围确认清单 |
| 5 | 成本管控 | ✅ 已完成 | `staff_levels.daily_cost` 接入工时×费率推导；预算 vs 实际跟踪；成本超支动态告警 |
| 6 | 报价排期 | ✅ 已完成 | 人天估算引擎（复杂度→工时×历史系数，替换写死 8h）；里程碑计划 + 整体时间线 |
| 7 | 进度管理 | ✅ 已完成(7d11029) | 里程碑进度视图；阻塞/风险卡点；「每日站会摘要」技能替代后台调度（遵守无后台进程规则） |

> **7 大方向全部完成。** 共新增 6 个技能（质量把控/产品交付/复盘总结/需求评估/成本管控/报价排期/进度管理——其中需求评估为增强既有 `project_evaluator`，故新脚本 6 个）+ 2 个数据模型（`Delivery`/`AssetVersion`）+ 一组 `DatabaseManager` 增量方法。全流程 pytest 392 passed / 11 skipped / 0 failed（覆盖率过 50% 门禁），ruff 全绿，streamlit 启动 HTTP 200。

## 意图路由接线（agent.py，已本地提交）
- 在 `agent.py` 为全部 7 个技能补齐意图路由：`INTENT_KEYWORDS` / `SKILL_ROUTE_SIGNALS` / `INTENT_EXAMPLES` 各加 6 项（质量把控此前已加），`_extract_inputs` 加 6 个分支，`_format_skill_result` 加 6 个可读 markdown 分支（按结果 key 判别动作，无需改技能本身）。
- 路由门槛：`_is_high_confidence_skill_request` 要求 `SKILL_ROUTE_SIGNALS` 中 action+entity 同时命中；示例短语均按此设计，embedding/LLM 层亦能命中。
- 测试：`tests/test_agent_routing.py`（14 用例，绕过 `__init__` 用最小实例验证检测/提取/格式化）。全流程 pytest 392 passed（含新增 14）。
- 提交：`agent.py` + `test_agent_routing.py`；**未触碰** `config.*`/`utils/*`/其他 WIP 测试文件。

## 已落地细节（Phase 1 质量把控）
- 新增 `artpm_agent/skills/quality_control_skill.py`：动作 `submit` / `review` / `report`。
  - `submit_for_review`：任务状态 → 待审核。
  - `record_review`：accept→已完成+质量分；reject→进行中+返工计数+1；revise→待审核+返工计数+1。
  - `quality_report`：通过率 / 平均质量分 / 待返工 / 低分清单。
  - QA 阈值优先读 `config.qa`，缺失回退内置 `DEFAULT_QA_CONFIG`（**不强制改配置文件**）。
- `DatabaseManager` 补 `get_task` / `get_tasks` / `update_task`（支撑评审持久化）。
- `skill_router.py` 注册 `quality_control`；`agent.py` 加质检/验收/驳回意图（用户 WIP，未纳入提交）。
- `tests/test_quality_control.py`：9 用例，fake DB 无需真实库。
- 验证：pytest 341 passed / 11 skipped / 0 failed（覆盖率门禁通过）；ruff 全绿；streamlit HTTP 200。

## 已落地细节（Phase 2-7）

### 需求评估(aa88e9a)
- 新增 `requirements_assessment_skill.py`：`assess_complexity`（资产类型基线 + 需求关键词启发式判定 simple/medium/complex）、`scope_checklist`（资产清单/参考图/风格/交付格式/验收标准/周期/版权/沟通节奏）、`ingest`（解析报价自动建 Project+Assets+Document，修落库断层）。
- 验证：7 用例 passed，ruff 全绿。

### 成本管控
- 新增 `cost_control_skill.py`：`estimate`（接 `config.cost_config.staff_levels.daily_cost` 做工时×人天费率+管理费+税）、`budget`（报价 vs 实际，剩余与利用率）、`overrun`（达阈值告警 warning/critical）。配置走内置默认 + 可选覆盖。
- 验证：8 用例 passed，ruff 全绿。

### 报价排期
- 新增 `quote_scheduling_skill.py`：`estimate_man_days`（复杂度→基准工时 simple 8h/medium 24h/complex 60h，乘数量与历史系数）、`build_schedule`（按工作日推进排起止时间线，跳周末）、`milestone_plan`（启动/阶段交付/验收里程碑）。
- 验证：7 用例 passed，ruff 全绿。

### 进度管理(7d11029)
- 新增 `progress_management_skill.py`：`milestone_view`（按状态聚合 + 完成度）、`blockers`（逾期或长期零进度任务）、`standup_summary`（手动/触发式站会摘要，替代后台调度，遵守无后台进程规则）。
- 验证：5 用例 passed，ruff 全绿。

### 产品交付(fee24d4)
- `models.py` 新增 `Delivery`/`AssetVersion`；`DatabaseManager` 补 `create_delivery`/`get_project_deliveries`/`create_asset_version`/`get_asset_versions`。
- 新增 `delivery_skill.py`：`manifest`（绑项目列资产状态/最新版本/验收标准）、`acceptance`（逐资产生成验收条目）、`record_delivery`/`record_version`。
- 验证：6 用例 passed，ruff 全绿。

### 复盘总结（本轮）
- `DatabaseManager` 补 `add_knowledge`/`save_document`/`get_documents`。
- 新增 `retrospective_skill.py`：`summarize`（时间线/成本偏差/质量均分/准时率/经验教训）、`deposit_lessons`（经验教训自动写入 KnowledgeBase 沉淀）。
- 验证：4 用例 passed，ruff 全绿。

## 设计纪律（贯穿 7 个方向）
1. 复用已有字段与技能，不重复造轮子（如 QC 复用 `Task.quality_score`）。
2. 新增配置项走「内置默认值 + 可选 config 覆盖」，不强制改动用户 WIP 的 `default_config.json`。
3. 涉及 `agent.py`/`config.*`/`utils/*` 的改动保持在**用户 WIP 文件之上做最小增量**，备份后编辑，绝不丢弃其未提交业务逻辑。
4. 每个方向配单测 + 全流程 pytest + ruff + streamlit 冒烟三道闸门。
5. 本地提交、不推远程（用户要求）。
