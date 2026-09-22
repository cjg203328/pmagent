# Current Project Status

Validated on 2026-09-19 from branch
`chore/consolidate-uncommitted-work`.

产品定位与范围以 [`../product/PRD.md`](../product/PRD.md) 为准。本文件只记录
**平台层**已验证边界与待完成的外部验证。

## Business Reality Check（2026-09-19 实测）

平台管道已验证，业务闭环尚未验证。对 `data/` 的直接查询结果：

| 对象 | 行数 | 含义 |
| --- | --- | --- |
| `projects` / `assets` / `tasks` / `quotes` / `deliveries` / `staff` / `task_assignments` | 0 | 业务实体表基本全空 |
| `team_members` | 1 | 唯一记录是 2026-08-12 的空字段测试数据 |
| `workflow_runs` / `workflow_events` / `workflow_approvals` | 0 | 工作流执行引擎从未运行 |
| `knowledge_resources` / `knowledge_versions` / `wiki_pages` | 0 | 知识库管道通但无内容 |
| `permission_requests` | 0 | 审批边界从未被触发 |
| `workflow_definitions` | **3** | R-0 已修：此前每次启动 +3（一度达 123），现幂等；`test_workflow_builtin_version_loop.py` 防回归 |
| 业务技能命中率 | 0 / 3 | 2026-09-19 的 3 条真实业务请求 `intent=None`，全部由 LLM 兜底 |
| 交付物产出 | S0 路径已打通 | 「仅11月汇总版」现经 `run_turn` 产出可下载 `.xlsx`（此前只得到聊天内 markdown） |

fast 套件实测（2026-09-20）：**1864 passed, 7 skipped, 0 failed**（91 deselected）。
相比收缩迁移手册 §0 记录的 1673 passed 基线，新增来自 S0 回归（含走 `run_turn` 的端到端
断言）、费率模型、播种脚本、Job 服务、golden 结构断言与知识 owner 隔离断言。

7 个 skip 的构成：6 个是 `tests/test_postgresql_rls_integration.py` 因缺
`POSTGRES_TEST_URL` 跳过，1 个是 `tests/golden/test_structure.py` 的 T1
报价单生成器未实现。

同时段内的实测口径：

| 检查 | 结果 |
| --- | --- |
| 全量 fast 套件 | 1864 passed, 7 skipped, 0 failed |
| 报价门禁 `scripts/quote_golden_check.py`（结构） | 13 passed, 1 skipped，退出码 0 |
| 报价门禁 `--values`（真值） | 退出码 **3**：`cases/` 为空，未验证 |
| `ruff check artpm_agent tests scripts` | All checks passed，退出码 0 |
| `python -m compileall -q artpm_agent scripts` | 退出码 0 |
| `alembic heads` | 单一 head `f6a7b8c9d0e1` |
| 三栏原型（无头 Chrome，51 条断言） | 51 passed, 0 failed，无 JS 报错 |

> **对最后一行的限定**：`prototype/app.js` 的 `fetch(` 出现次数为 **0**，也没有任何
> `/v1/*` 调用。那 51 条断言验证的是静态原型的 DOM 行为与视觉状态，
> **不构成「三栏已可与后端集成」的证据**，也不回答 PRD §7.4 R-UI 的问题
> （Streamlit 能否承载固定成果栏与独立滚动）。M1.5 spike 仍未完成。

## S0 交付物管道（2026-09-20 复核）

S0 已接入请求路径：`ArtifactCoordinator._generate_monthly` 在检测到「月份 + 保留/
汇总/导出类动词 + 上传了工作簿」时直接调用 `generate_xlsx_monthly`，产物经
`verify_artifact("xlsx_monthly")` 核验后进入下载卡片。此前该生成器**没有任何生产调用方**
（`coordinator.py` 中 `xlsx_monthly` 出现 0 次），所以功能存在但用户永远触发不了。

回归锚点：`tests/test_s0_monthly_workbook.py::test_run_turn_delivers_a_monthly_workbook`
走 `run_turn()`，断言产出可下载 `.xlsx` 且核验 `passed`；该路径为确定性实现，
测试用一个会抛异常的 LLM 桩证明**不需要模型**也能交付。

| 环节 | 状态 | 证据 |
| --- | --- | --- |
| Excel 分类判别 | 已修 R-3：判别与客户识别解耦，未知客户的报价表不再降级为 `unknown` | `tests/test_s0_monthly_workbook.py` 分类 3 条 + `test_regressions.py` |
| 按月份裁剪 | 已实现：保留表头格式与列宽，区分「本月新增」与「累计」 | `transform_monthly_workbook` 9 条断言 |
| 宽表（月份在 sheet 名上） | 已修 R-5：真实附件 `角色组-任务及绩效分配.xlsx` 的 13 个月份 sheet 现可正确裁剪 | `sheet_target_month` + `_select_sheets_by_month` |
| 请求路径接入 | 已修 R-4：`_generate_monthly` 接入 coordinator，走 `run_turn` 端到端 | `test_run_turn_delivers_a_monthly_workbook` |
| XLSX 生成与核验 | 已实现：核验重新打开、口径说明、源文件 SHA-256、发布副本 SHA-256 | `generate_xlsx_monthly` + `verify_artifact("xlsx_monthly")` |
| 结构性 golden 断言 | 已落地 ST-1..ST-7，13 passed / 1 skip | `tests/golden/test_structure.py` |
| 报价真值断言 V-1..V-3 | **阻塞**：`tests/golden/cases/` 为空 | `--values` 退出码 3（未验证），由 `tests/test_quote_golden_check.py` 锁定 |

排查中发现并修复的实现缺陷：`xlsx_monthly.py` 的 `_MONTH_VALUE` 正则分支顺序把
`1[0-2]` 排在 `0?[1-9]` 之后，导致 `2026-11` 被解析为 `2026-01`；若不经断言，
按月份裁剪会静默保留错误月份的行。

剩余的 1 条 golden 跳过项是 T1 报价单生成器未实现（交付物模板库 §4）。
ST-3 与 ST-4 原为跳过占位，在费率单点化落地后已改为真断言（见下节）。

## 费率单点化（2026-09-19 实测）

[`../product/领域数据契约.md`](../product/领域数据契约.md) §1 要求的唯一费率来源
已落地：`team_members.daily_cost`。

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 迁移加列 | 已落地 | `alembic/versions/0005_team_member_rate_fields.py`，head `e5f6a7b8c9d0` |
| ORM 映射 | 已落地 | `database/models.py` 的 `team_members` 含 6 个新列；`projects` 含 `payment_terms_days` |
| 费率领域模型 | 已落地 | `skills/rate_model.py`：`resolve_member_rate` / `supplier_multiplier` / `asset_baseline` / `revision_cost` / `payment_terms_cost` |
| 技能层去常量 | 已完成 | `cost_control_skill.py` 已删除 `DEFAULT_STAFF_LEVELS` / `DEFAULT_OVERHEAD_RATE` / `DEFAULT_TAX_RATE` |
| 缺费率行为 | 已完成 | `estimate` 返回 `needs_input` 并列缺项，不再回退内置常量 |
| 播种脚本 | 已落地 | `scripts/seed_member_rates.py`，默认 dry-run，`--apply` 才写入，只填 NULL |
| 播种测试 | 已落地 | `tests/test_seed_member_rates.py`，13 条 |

排查中发现并修复的实现缺陷（两处，均由测试抓出）：

1. `TeamMember.cost_source` 原先带 `default="default"`，会让从未标定费率的人
   声称费率来自默认配置，与迁移注释「NULL 表示未标定」自相矛盾；已去掉模型级默认值。
2. `_resolve_member` 的「同档位多价」歧义检查误用 0~1 比例校验器判人天费率，
   500 与 900 全被判无效，等于把歧义静默放行；已改用金额校验 `normalize_daily_cost`。

配置层：`default_config.json` 的 `staff_levels` 已降级为**仅播种用**（加 `_note` 说明
运行时不再读取），并补齐 `supplier_coefficients` / `asset_baselines` / `revision_policy` /
`payment_terms` 四段结构，数值一律 `null`（未标定）。

### 真实库迁移与播种（2026-09-19 23:0x 执行）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 备份 | `python scripts/backup_data.py` | `data/backups/20260919_230057/`，10 个 `.db`，源文件未删 |
| 迁移 | `python -m alembic upgrade head` | `d4e5f6a7b8c9` → `f6a7b8c9d0e1`，两段迁移均执行 |
| 播种预演 | `python scripts/seed_member_rates.py` | 待播种 0 人，跳过 1 人 |
| 播种执行 | `python scripts/seed_member_rates.py --apply` | 写入 0 行，1 人仍无费率 |

迁移后的真实库核对（只读）：

- `alembic current` = `f6a7b8c9d0e1 (head)`；重复执行 `upgrade head` 退出码 0，无重复升级。
- `PRAGMA integrity_check` = `ok`，18 张表。
- `jobs` / `job_artifacts` 已建（各 0 行）；`team_members` 6 个费率列与
  `projects.payment_terms_days` 均已存在。
- DDL 与 ORM 映射逐表比对：`jobs`(19/19)、`job_artifacts`(14/14)、
  `team_members`(23/23)、`projects`(22/22) 全部一致，无漂移。
- `job_artifacts` 的唯一约束 `uq_job_artifact_version` 与
  `ON DELETE CASCADE` 外键均已按定义创建。

**播种结果为 0 行的原因**：库中唯一成员 `小李` 的 `skill_level` 为 `NULL`，脚本按设计
不猜档位，直接列入跳过清单。因此**真实库上报价仍返回 `needs_input`**——这是缺数据时
的既定行为，不是故障。要让费率生效，需先补 `skill_level` 再重跑 `--apply`，
或直接为该成员标 `daily_cost` + `cost_source`。

端到端只读验证：`CostControlSkill.estimate(hours=16, staff_level="中级")` 对真实库
返回 `status=needs_input`、`success=false`、`missing` 含 `member`，响应中**无** `total_cost`
字段——内置常量确已不再参与计价。

## R-0 内置工作流版本自增：已闭环（2026-09-20 实测）

[`收缩迁移手册`](收缩迁移手册.md) §1（S-1）。

根因：`definition_checksum` 把 `definition.model_dump()` 整段做哈希，其中含
`version`。于是「内容变 → 发布新版 → 版本号变 → checksum 又变 → 下次启动仍判定
已变更」，自持循环，每次启动每个内置 +1 行。真实库 `conversations.db` 实测
3 个内置各累积 41 版，共 123 行。

| 项 | 状态 | 证据 |
| --- | --- | --- |
| checksum 收窄 | 已落地 | `workflows/store_codec.py` 的 `CHECKSUM_EXCLUDED_FIELDS` = `version` / `tenant_id` / `workspace_id` / `profile_id` |
| 落库 JSON 未收窄 | 已确认 | `definition_json` 仍含 `version` 与 scope 字段；体积上限仍作用于完整 JSON |
| 回归测试 | 已落地 | `tests/test_workflow_builtin_version_loop.py`，9 条 |
| 数据清理脚本 | 已落地 | `scripts/clean_phantom_workflows.py`，默认 dry-run |
| 清理脚本测试 | 已落地 | `tests/test_clean_phantom_workflows.py`，12 条 |
| 真实库清理 | **已执行** | `data/conversations.db` 123 行 → 3 行 |

真实库清理实录：

```text
备份        data/backups/20260920_031511/（10 个 .db，源文件未删）
预演        待删 120 行，孤儿 override 0 行，workflow_runs 0 行
执行        已删除 120 行重复定义、3 行陈旧副本、120 行孤儿 override
结果        progress_check / quote_assessment / reminder_dispatch 各 1 行
稳定性      重开 WorkflowStore 3 次后仍为 3 行，overrides 3 行
完整性      PRAGMA integrity_check = ok
```

清理前的安全门（手册 §1 步骤 3、5 的机器化）：待删行必须全部 `source='builtin'`
且 `read_only=1`；库中不得有用户自建工作流；不得有 run 引用**将被删除**的版本。
任一不满足即中止且不写库，退出码 1。

排查中修正的两处实现错误（均由测试抓出）：

1. 安全门初版把「所有 checksum 相同的副本」都当作会保留，而 prune 实际只留
   版本号最小的一行；于是一个引用 v2 的 run 会被判为安全，正好绕过步骤 5 的拦截。
   已改为与 prune 同规则。
2. run 拦截初版写成「有 run 就停」，但引用保留版本的 run 属正常流量，会导致清理
   永远无法执行。已改为只统计引用被删版本的 run，并补反向断言。

同日早些时候 `tests/test_p2_single_main_chain.py::test_run_turn_sequences_full_handler_chain`
曾失败（断言源码文本 `inject_memory_context(ctx`，而实现重构为 `_inject_memory_context(`）；
`harness/turn_service.py` 于 15:24 更新后已恢复通过。该测试仍属于**源码文本嗅探式**
契约测试，任何等价重命名都会再次打断它——见
[`../architecture/OPTIMIZATION_STRATEGY.md`](../architecture/OPTIMIZATION_STRATEGY.md) §7
与 PRD §7.5 的改进建议。

## S-2b 知识 owner 维度：已闭环（2026-09-20 实测）

[`收缩迁移手册`](收缩迁移手册.md) §2b。本期唯一一处「为未来而做」的改动，理由是该
维度只能趁表还空的时候加：等真实知识入库后再补，就要回填并重判全部既有行的可见性。

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 迁移加列 | 已落地 | `memory/knowledge_migrations.py` v8，`_ensure_knowledge_owner_columns` |
| 列与索引 | 已落地 | `knowledge_resources` / `knowledge_rules` 各加 `visibility`、`owner_principal_id`，并建 `(tenant_id, workspace_id, visibility, owner_principal_id)` 索引 |
| 检索过滤 | 已落地 | `knowledge_search_service.py` 的 `search()` 与 `iter_active_resources()` 均强制带 owner 子句 |
| 写入路径 | 已落地 | `ingest_resource` / `propose_rule` / `propose_ingestion` 接受 owner；收尾提议默认 `private`，归属取可信 scope |
| 记录回显 | 已落地 | `_resource_record` / `_rule_record` 返回 `visibility` 与 `owner_principal_id` |
| 隔离断言 | 已落地 | `tests/test_knowledge_owner_isolation.py`，17 条 |
| 真实库迁移 | **已执行** | `data/conversations.db` v7 → v8 |

真实库迁移结果（只读核对）：

```text
备份        data/backups/20260920_035812/（10 个 .db，源文件未删）
迁移        knowledge_schema_migrations 1..7 → 1..8
列          两表均出现 owner_principal_id / visibility
旧行语义    2 条 knowledge_rules 全部 visibility='workspace'，owner=NULL
完整性      PRAGMA integrity_check = ok
```

隔离语义（`tests/test_knowledge_owner_isolation.py` 覆盖）：`visibility='workspace'`
人人可见；`visibility='private'` 仅 owner 可见。匿名调用者（principal 为 NULL）只能
看到共享行——SQL 中 `owner_principal_id = NULL` 恒不成立，所以「没有身份」不会被
当成「是本人」。

排查中修正的四处实现错误（均由测试或探针抓出）：

1. owner 子句初版拼成 `r.{clause}`，而 clause 自带括号，生成 `r.(a OR b)` 这种
   SQLite 语法错误，10 条知识测试直接失败。已改为按表别名生成子句。
2. `_ingest_prepared_resource` 新增的两个参数变成必填，而提议确认路径没传，
   4 条测试报 `missing 2 required keyword-only arguments`。已把 owner 随提案
   一并冻结并读回。
3. `_resource_record` 未回显两个新列，写入成功但记录里是 `None`——探针发现。
4. 资源记录初版把「可见性」只当写属性，读取侧看不到归属，无法在 UI 上说明
   「这条是谁的」。

## S-6 仓库杂物清理：已闭环（2026-09-20 实测）

[`收缩迁移手册`](收缩迁移手册.md) §6。手册列的 8 项里，实测只有 2 项实际存在。

| 项 | 手册要求 | 实测终态 |
| --- | --- | --- |
| `providers/gateway.py.backup` | 删除 | 已不存在 |
| 根目录 `nul` | 删除 | 不存在 |
| `OUTBOX_RLS_COMPLETION_REPORT.md` | 移到 `docs/reports/` | **已移动**（`git mv`，git 识别为重命名，历史保留） |
| `.coverage` | 加 ignore 并从索引移除 | 未被跟踪，`.gitignore:65` 已忽略 |
| 根目录 `config.json` | 确认后处置 | 不存在 |
| `build/`（4.1 MB） | 加入 ignore | `.gitignore:7` 已忽略 |
| `.cache/`（165 MB） | 加入 ignore | `.gitignore:73` 已忽略 |
| `ruff.toml` 僵尸豁免 | 删除 | **已清空 `exclude`** |

`ruff.toml` 的 `exclude` 原有 11 项：10 项指向不存在的路径，1 项
（`core/mcp_skills.py`）文件存在但用 `ruff check --isolated` 复核无告警。全部移除后
`ruff check artpm_agent tests scripts` 仍为 All checks passed，证明原先的豁免没有
掩盖任何现存问题。

僵尸豁免的危害是反向的：被排除的路径一旦重新创建就**不会**被检查，而门禁照旧显示
通过。这是「门禁看起来比实际更宽」的典型形态，与 §7 要防的「全绿但测的是不存在
的东西」是同一类问题。

## S-4 存储合并：前置调查完成，合并未执行（2026-09-20）

手册 §4 假设「本期只合并空库」，但实测有三处分歧，涉及被 PRD §5 冻结的族，因此
未执行任何路径改动。

**成立的部分**：三库确实都是 0 行——`feedback.db` 0 行/2 表、`strategies.db` 0 行/2 表、
`memory.db` 0 行/10 表。

**分歧 1：`memory.db` 装的是业务表，不是记忆表。**

```text
memory.db 表：projects, tasks, task_assignments, staff, quotes,
             progress_updates, reminders, documents, operation_logs
```

表名与 `artpm.db` 高度重叠，与 `conversations.db` 的知识/会话/工作流表完全不同。
并入后会得到第三套 `projects` / `tasks` 表。

**分歧 2：`strategies` 属于手册自己要求冻结的族。**
其实现是 `artpm_agent/evolution/strategy_store.py`，即 PRD §5「冻结」的自我进化族；
手册第 4 条要求该族本期不动，第 1 条却把它列入合并对象。

**分歧 3：三个库的路径都不是配置项。**
`feedback` 与 `strategies` 由 `resolve_state_path(文件名, 环境变量名)` 硬编码决定，
没有配置键；手册要求「保留配置项可覆盖」与实测不符。

这三处分歧均需在动冻结族之前澄清，属于**流程性阻塞**而非执行失败。

## Runtime Shape

- Streamlit remains the primary local UI.
- FastAPI is the authenticated REST gateway.
- `ArtPMAgent` is a compatibility facade over `RequestOrchestrator`.
- `LocalHarnessRuntime` owns request-scoped services and tenant binding;
  `run_turn()` is the canonical request-processing boundary for API, UI and
  CLI turn execution.
- `HarnessRuntime` is the provider-neutral contract and
  `LegacyAgentRuntimeAdapter` is compatibility-only.
- SQLite and FAISS are the offline defaults. PostgreSQL, Qdrant, Redis,
  telemetry, and Sentry are deployment-selected integrations.
- RuntimeFactory scopes workflow/artifact runtimes by tenant, workspace and
  profile with bounded TTL/LRU cleanup; UI session state is only a compatibility
  alias.
- Canonical tool calls run through the Harness AgentLoop with JSON-schema
  validation, bounded turns/calls/workers, per-call timeout, cancellation,
  side-effect gates, scoped result spill and durable SessionStore events.

## Quality Commands

```powershell
python -m pytest -q --no-cov -m "not integration and not benchmark and not slow"
powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_integration.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_benchmark.ps1
powershell -ExecutionPolicy Bypass -File scripts/coverage_core.ps1
python scripts/mypy_ratchet.py
python scripts/build_graph.py --selftest
# Full-repository blocking correctness rules
ruff check artpm_agent tests --select E9,F63,F7,F82

# Changed Python files also run the normal project rule set
ruff check <changed-python-files>
```

The fast suite excludes external integrations, benchmarks, and expensive
Streamlit startup tests. The offline suite includes the slow UI tests. Coverage
is a separate focused 90% gate over the modernization boundary modules;
integration tests require explicit credentials or services and must never be
treated as offline tests.

Unrestricted whole-repository Ruff still reports historical modernization and
style debt. It is governed as a changed-surface ratchet: new or edited focused
modules must pass the normal rule set, while the repository-wide blocking gate
always covers syntax errors, invalid constructs, and undefined names.

## Current Boundaries

- API chat is an async route. Native async handlers can be injected through
  `GatewayServices.chat_async_handler`; the legacy synchronous handler runs in a
  worker thread until provider clients are migrated.
- Remote and local MCP blocking work is isolated from the host event loop;
  transport and tool return contracts remain backward compatible.
- API transcripts persist stable error codes rather than raw provider
  exceptions.
- Request dependencies are grouped in `RequestServiceBundle` while old
  `RequestOrchestrator` private methods remain compatibility delegates.
- `VectorBackend` defines the shared local/remote vector-store contract.
- Rule approval and rejection calls can be bound to an explicit workspace;
  older direct calls remain compatible when no workspace is supplied.
- `/ready` probes all authoritative Store contracts through `StorageRegistry`;
  optional model/OCR/MCP/vector capabilities are reported separately.
- API permission/workflow routes live in dedicated router modules; gateway and
  turn dependencies are expressed through Protocol ports. The remaining
  workspace/chat/embed/voice extraction is intentionally the next compatibility
  wave.

## Remaining External Verification

- PostgreSQL RLS requires a disposable PostgreSQL instance and a non-owner app
  role. Without one, the RLS integration test must remain an explicit skip.
- Live MCP verification requires `ART_ENABLE_INTEGRATION=1` and a valid
  Skills Forge configuration.
- Provider latency and failover need a controlled external model environment;
  offline tests must continue using fakes.
- The full-tree mypy ratchet baseline is 451 reviewed legacy diagnostics,
  measured with the pinned `mypy==2.3.1` command in `scripts/mypy_ratchet.py`.
  The previous 363 baseline predates the later memory/UI/API commits; 47
  diagnostics in this worktree's changed files were fixed before ratcheting.
  The four P2 packages (`runtime`, `harness`, `api`, `tenancy`) remain the
  strict zero-error gate, and the baseline only prevents further growth.


## 办公流与意图优先级（2026-09-20）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| §4.5 执行意图优先门 | 已实现 | `turn_service._EXECUTION_INTENT` 动词门；`tests/test_intent_priority_gate.py` 3 条次序回归 |
| `weekly_report` 技能 | 已实现 | 读 `tasks`/`team_members` 真实数据，交付核验通过的 `.docx`；`tests/test_weekly_report_skill.py` 含走 `run_turn` 的端到端断言 |
| 技能注册面 | 已补齐 | 关键词表 / 信号表 / 示例表 / LLM 描述表 / 非法参数表 / 输入 schema / formatter 共 7 处；`test_route_recall.py` 与 `test_builtin_tool_schemas.py` 的技能数不变量从 17 更新为 18 |
| M1.5 三栏 spike | 已完成 | Streamlit 1.59 `st.container(height=...)` 原生支持独立滚动；DOM 实测滚一栏另两栏不动。结论：留在 Streamlit，无需 iframe/独立页 |

权限语义说明：`weekly_report` 标为 `read_only=True`。它不修改任何业务表，只产出
版本化交付物，与 artifact 管道同级；`access_decision` 对非只读技能要求
permission store + full 模式才放行，标错会导致每次生成周报都弹审批。


## Job 接入生产入口（2026-09-20）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| `StorageRegistry.jobs` | 已接入 | 复用 `business` 的 DatabaseManager，进程内单例 |
| `GatewayServices.jobs` | 已接入 | dataclass 末尾可选字段，未注入时 `/v1/jobs` 返回 503 |
| `/v1/jobs` 三条路由 | 已接入 | `api/routers/jobs.py`；`tests/test_jobs_api.py` 覆盖 201/200/404/401 |
| `weekly_report` → Job | 已接入 | 建 Job(running) → 挂交付物 → `succeeded`；生成失败转 `failed`，不留半状态 |

门禁终态：fast 套件 **1864 passed / 0 failed**；阻塞 lint 全过；compileall 通过；
mypy 棘轮 **451 = 基线**，strict 四包（runtime/harness/api/tenancy）通过。

### 推送前已知的非阻塞遗留

- `KnowledgeRuleService` 有 9 个被调用但未定义的方法（`_connection`、`_utc_now`、
  `_resolve_scope`、`_resolve_visibility` 等）。**全仓无人实例化该类**，属提交时就存在的
  死代码；mypy 报 1 条 attr-defined。未猜补实现——补 9 个方法等于替别人完成半成品设计。
- `knowledge_migrations.py` 的 8 条 `_utc_now` attr-defined 是 **mypy 对 mixin 的误报**：
  运行时由 `WorkspaceKnowledgeStore` 的 MRO 提供该方法，全新库迁移实测通过。
- 三栏 UI 骨架、S1 端到端、其余办公流技能未做（M2/M3.5 剩余部分）。
