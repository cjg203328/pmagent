# 下一步行动

Status: current
Owner: pmagent maintainers
Review cadence: 每个里程碑结束时

本文件只列**当前一周**的动作。范围、优先级和出口判据一律以
[`../product/PRD.md`](../product/PRD.md) 为准，本文件不定义产品方向。

> 2026-09-19 重写说明：本文件此前内容是 2026-07-22 的多租户 / GraphQL / Webhook
> 实施清单，其中多租户已完成、GraphQL 与 Webhook 已被 PRD §5 列入收缩范围，
> 并且文末残留了一段可直接执行的 `git add` / `git commit` / `git push` 指令。
> 该版本已整体作废。**任何文档都不应包含可直接执行的提交与推送指令**；
> 提交与推送必须由人显式确认。

## 当前主线

定位：企业级业务助手 · 美术项目经理。界面为三栏工作台（左任务 / 中过程 / 右成果）。
主轴是**接上，而不是加满**。

## M1（本周）：修缺陷 + 收敛数据模型

按 PRD §11 M1 与 §10 缺陷清单。**进度以 2026-09-19 实测为准**（见
[`../operations/CURRENT_STATUS.md`](../operations/CURRENT_STATUS.md)）：

1. **R-0 内置工作流版本自增死循环** — checksum 排除 `version`/`tenant_id`/
   `workspace_id`；清理历史幻影版本。实测单日已从 111 行涨到 123 行，越晚修数据越多。
   **状态：未开始。**
2. **R-3 Excel 附件分类硬编码** — `parsers/excel_parser.py` 停止对任意 Excel
   返回「报价单」，改按表头/结构判别，判不出返回 `unknown`。
   **状态：已完成**（判别与客户识别解耦；`tests/test_s0_monthly_workbook.py` 与
   `tests/test_regressions.py` 覆盖）。
3. **R-4 文件类请求接通交付物管道** — 让「导出/另存/汇总版」命中 `artifacts/`
   XLSX 生成与核验，产出可下载文件而不是聊天内 markdown。
   **状态：部分完成**——`generate_xlsx_monthly` 与核验已实现并测试通过，
   但**尚未接入 UI/意图路由**，用户仍拿不到文件。
4. **人员模型收敛** — 保留 `team_members`，并入 `daily_cost`，废弃 `staff` 与
   `task_assignments`；删除 `skill_router.py:538` 的 `or` 兜底与三处 `hasattr` 阶梯。
   ⚠ 需先走 `docs/operations/收缩迁移手册.md`，并需业务方确认走 `team_members`。
   **状态：部分完成**——`daily_cost` 与 `contact_wecom` 已并入 `team_members`
   （迁移 `0005`），但 `staff` / `task_assignments` 未废弃，`skill_router` 的兜底未删。
5. **费率单点化** — 唯一来源 `team_members.daily_cost`；删除
   `cost_control_skill.py:19-27` 硬编码与 `config_data/default_config.json:95` 副本。
   **状态：已完成**（技能层三个常量已删除，`estimate` 缺费率返回 `needs_input`；
   播种脚本 `scripts/seed_member_rates.py` 已就位；`staff_levels` 降级为仅播种用）。
   **真实库已迁移已播种**（2026-09-19）：`data/artpm.db` 升到 `f6a7b8c9d0e1`，
   `jobs` / `job_artifacts` 已建；播种写入 0 行，因为库中唯一成员 `skill_level` 为
   `NULL`，脚本按设计不猜档位。**该库上的报价因此仍返回 `needs_input`**，
   需先补档位再重跑 `--apply`。

出口判据：报价技能读同一张表；Excel 不再一律判成报价单；S0 能产出 `.xlsx`；
fast 套件全绿。

**当前实际达成**：Excel 不再一律判成报价单 ✅；S0 管道能产出并核验 `.xlsx` ✅
（未接 UI）；fast 套件 1840 passed / 0 failed ✅；报价技能读同一张表 ✅
（代码路径已通，真实库因缺档位数据仍返回 `needs_input`）；Job 对象与三栏原型 ✅。

**当前未达成**：`data/artpm.db` 无可用费率数据；S0 未接 UI 入口；报价真值断言
V-1..V-3 因 `tests/golden/cases/` 为空未成立；收缩迁移手册的能力删除未执行。

## M1.5（与 M1 并行）：三栏技术 spike

PRD §7.4 R-UI。验证 Streamlit 能否承载固定成果栏与独立滚动，三选一给出可演示结论：
组件级方案 / iframe 嵌预览 / 独立页 + 深链。

出口判据：结论明确，且据此更新 PRD §12.3（是否脱离 Streamlit）。

## 并行推进（非代码）

- 补齐 `docs/product/领域数据契约.md` 中的真实费率与资产类型基线 —— 需要业务方数据。
- 建立 `tests/golden/` 报价正确性评测集 —— 需要一位真做过美术外包报价的人提供
  3 份历史项目及其真实成交价。**这是 PRD §12.5 记录的硬阻塞，不解决则 M2 之后
  所有里程碑无法判定完成。**

## 不要做

以下在 PRD §5「明确不做」清单内，不要在本周动它们，也不要"顺手完善"：

- GraphQL API、通用 Webhook 平台、插件市场（`ADVANCED_FEATURES_ROADMAP.md` 旧内容）
- 实时语音、embed 通道、插件系统、远程 MCP、Qdrant 远程向量、Redis
- 自我进化族功能扩展（reflection / meta_memory / strategies / consolidation / episodes）
- 整仓 Ruff 风格清理（按 `docs/operations/QUALITY_GATES.md` 的 changed-surface 棘轮执行）

## 验证命令

```powershell
python -m pytest -q --no-cov -m "not integration and not benchmark and not slow"
python scripts/mypy_ratchet.py
ruff check artpm_agent tests --select E9,F63,F7,F82
```

完整门禁清单见 [`../operations/QUALITY_GATES.md`](../operations/QUALITY_GATES.md)。
