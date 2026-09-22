# Product Prototypes

Store product prototypes and interaction specifications here. Runtime source
code does not belong in this directory.

## 当前内容

| 文件 | 用途 |
| --- | --- |
| [`交互规格.md`](交互规格.md) | 三栏工作台（左任务 / 中过程 / 右成果）信息架构、断点、Job 状态机、关键流程 F1–F4、验收清单 A1–A8 与实现风险 |
| [`index.html`](index.html) | 三栏工作台可跑原型（语义化结构，无外部依赖） |
| [`styles.css`](styles.css) | 原型样式与设计令牌（`--pm-*`），含桌面 / 平板 / 手机三档断点 |
| [`app.js`](app.js) | 原型交互逻辑：任务分组、step 五态、澄清与审批卡片、成果版本、定点改 |

### 打开方式

直接双击 `index.html` 即可，无需构建或本地服务器。数据在内存中，刷新即重置。

### 原型覆盖的验收项（2026-09-19 实测）

用无头 Chrome 对 `index.html` 跑 51 条断言，51 passed / 0 failed，无 JS 报错。
覆盖 A1（任务列表与会话分组）、A3（成果卡可下载）、A4（定点改不重解析附件）、
A5（澄清卡片可点选）、A6（计划中显示下次触发与历史 run）、A7（无对话介入时
创建、推进、完成定时 Job）、F1（新建任务弹窗）与三档断点。

**这不是产品验收。** 上表只说明原型页面自身的交互断言通过；PRD §7 的 I1–I6
需要在真实后端接线后重新验收，`交互规格.md` §9 的 Streamlit 风险仍未决策。

### 已知边界

- 原型不接后端，`Job` 状态推进与产物核验均为前端模拟。
- 「定点改」演示的是「只重跑对应 step 且不增加附件解析计数」这一条约束，
  真实实现依赖 `artpm_agent/jobs/service.py` 的 `attach_artifact` 版本追加。
- 三栏在 Streamlit 内如何落地尚未决策（`交互规格.md` §9 的 A/B/C/D 四案）。

产品范围与场景优先级见 [`../docs/product/PRD.md`](../docs/product/PRD.md)。
交付物字段与版式见 [`../docs/product/交付物模板库.md`](../docs/product/交付物模板库.md)。

## 约定

- 交互规格是**要求**，不是实现说明；实现契约仍写在 `../docs/architecture/`。
- 新增原型页面或交互稿放在本目录，不要堆到 `docs/` 根目录。
- 与 PRD 冲突时以 PRD 为准，并同步修订本目录文档。
