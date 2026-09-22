# 报价正确性评测集（golden）

Status: **结构断言已落地（2026-09-19）**；真值断言 **blocked — 等待业务方数据**
Owner: pmagent maintainers
关联：[`../../docs/product/PRD.md`](../../docs/product/PRD.md) §9、§12.5；
[`../../docs/operations/收缩迁移手册.md`](../../docs/operations/收缩迁移手册.md) §8

## 1. 为什么需要这个目录

当前 7 道质量门禁（Ruff、mypy 棘轮、coverage ≥20%、benchmark、PG RLS、
Compose smoke、架构契约测试）**全部只测平台健康度，没有一道断言「报价算得对」**。

`benchmarks/core_performance.py` 自己写明 "deliberately excludes network and model
calls"——它测延迟，不测正确性。

后果：可以交出一个「全绿但算错」的版本，而报价算错会直接发给客户。

## 2. 本目录**不放**什么

**不放编造的期望答案。**

如果用推测的费率基线生成 golden 文件，再让 CI 断言系统输出与之相符，那得到的
只是「系统和自己一致」的假绿灯，比没有测试更危险——它会让「报价功能已验证」
这句话显得有据可依。

因此本目录在拿到业务方真实数据前，**只有结构和结构类断言**。

## 3. 需要业务方提供什么

每个样本一份，共 3–5 份：

| 项 | 说明 | 为什么必须真实 |
| --- | --- | --- |
| `*.input.xlsx` | 甲方原始需求表（脱敏） | 覆盖真实解析难度 |
| `*.expected.json` | 人工确认的报价结果 | 唯一可信基准 |
| `*.expected.docx` | 当时实际发给客户的报价单 | 校验版式与字段 |
| 元信息 | 项目类型、资产构成、最终成交价、实际改稿轮次、实际人天 | 用于 S3 偏差回归 |

脱敏要求：替换客户名、人员真名、合同编号；保留数值结构。

## 4. 目录结构（约定）

```text
tests/golden/
├── README.md                  本文件
├── test_structure.py          ST-1..ST-7 结构断言（已落地）
├── schema/
│   └── expected.schema.json   expected.json 的 JSON Schema（待 cases/ 定稿）
└── cases/
    ├── g01-<项目代号>/
    │   ├── input.xlsx
    │   ├── expected.json
    │   └── meta.json          项目类型/成交价/实际改稿/实际人天
    └── ...                    为空时 quote_golden_check.py --values 退出码 3
```

## 4.1 退出码契约

CI 只认退出码，所以「没验」必须与「验过」在机器信号上区分开：

| 退出码 | 含义 |
| --- | --- |
| `0` | 结构断言全部通过 |
| `1` | 结构断言失败（pytest 的退出码） |
| `2` | 真值断言不可用：样本存在但比对器未实现 |
| `3` | 真值断言被要求执行，但 `cases/` 为空——**未验证，不是通过** |

`python scripts/quote_golden_check.py --values` 在当前（`cases/` 为空）返回 **3**，
因此任何把它当门禁的流水线都会红，而不是静默绿。该契约由
`tests/test_quote_golden_check.py` 的 10 条断言锁定。

## 5. 现在就能做的：结构类断言

不依赖业务真值，可在 M1 落地：

| # | 断言 | 抓的是什么问题 |
| --- | --- | --- |
| ST-1 | 给定含 N 个资产的输入表，报价明细表恰好 N 行，且列名与 `交付物模板库.md` §4 一致 | 漏项、列漂移 |
| ST-2 | 费用构成各项之和 == 小计；小计 × (1+管理费率) × (1+税率) == 总计（容差 0.01） | 算术自相矛盾 |
| ST-3 | 任一费率项的 `cost_source` ∈ {`contract`,`quote_history`,`manual`,`default`}，为 `default` 时产物中必须出现「未标定」标注 | 静默用默认值冒充实测值 |
| ST-4 | 无 `daily_cost` 的人员 → 返回 `needs_input`，**不产出报价文件** | 缺数据时瞎猜 |
| ST-5 | 生成的 `.xlsx`/`.docx` 能被重新打开，工作表/段落数与请求一致，SHA-256 与发布副本匹配 | 交付物损坏 |
| ST-6 | 同一输入两次运行，产物结构一致（金额、行数、章节顺序） | 非确定性 |
| ST-7 | Excel 附件分类不再一律返回「报价单」：给人天表应判为 `unknown` 或产能表 | PRD §10 R-3 |

ST-1..ST-7 全部可以现在写，且**每一条都能抓到当前代码的真实缺陷**。

## 6. 拿到业务数据后才能做的：真值断言

| # | 断言 |
| --- | --- |
| V-1 | 系统报价总额 与 `expected.json` 报价总额偏差 ≤ 约定容差（如 ±8%） |
| V-2 | 逐资产类型工时估算 vs 实际人天偏差 |
| V-3 | 引入 S3 回写规则后，第二次估算偏差 < 第一次（PRD §9 的收敛指标） |

## 7. 门禁接入

```powershell
python scripts/quote_golden_check.py           # 结构断言，M1 起阻塞
python scripts/quote_golden_check.py --values  # 真值断言，需 cases/
```

CI 中：结构断言作为必过 job；真值断言在 `cases/` 为空时**明确 skip 并打印原因**，
不得报告为已通过（沿用 `AGENTS.md` 对集成测试的诚实性要求）。

## 8. 阻塞状态

PRD §12.5：**需要一位真做过美术外包报价的人提供 3 份历史项目及其真实成交价。**

这件事没有解决之前：
- M2 之后的里程碑出口判据无法判定；
- 「报价测算」能力不应对外宣称已验证；
- README 与 PRD 中该能力必须保持「已实现未验证」标注。
