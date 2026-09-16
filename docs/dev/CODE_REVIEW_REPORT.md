# 代码审查报告 — `artpm_agent`

**日期**：2026-07-15
**审查范围**：`D:/桌面/xiangmu/pmagent/artpm_agent`（126 个 Python 文件，约 41,098 行）
**方法**：全量反模式扫描（eval/exec、subprocess shell、裸 except、可变默认参数、`.get()` 空解引用、SQL 注入、assert）+ 对最高风险核心模块的逐项深读（agent 编排、editing 子系统、artifacts/workflows/memory 存储、providers 网关、skills 路由、routing、ui_helpers、core）。

---

## 🔴  blockers（已修复，会导致崩溃/数据错误）

| # | 文件:行 | 问题 | 修复 |
|---|---------|------|------|
| 1 | `agent.py:1126` | `cost_config = self.config.get("cost_config")` 缺省返回 `None`，随后 `cost_config.get(...)` 抛 `AttributeError`，所有未配置 `cost_config` 的部署在报价计算时崩。 | 改为 `self.config.get("cost_config", {})` |
| 2 | `workflows/store.py:67-81` | `_connection` 中 `conn = self._connect()` 若抛错（路径非法/无权限），`finally: conn.close()` 触发 `NameError`，**掩盖真实 DB 连接错误**。 | `conn = None` 前置，`finally` 内 `if conn is not None: conn.close()` |
| 3 | `memory/workspace_knowledge_store.py:93-107` | 同 #2 的潜伏 bug（`connection` 未绑定 → `NameError` 掩盖真实错误）。 | 同 #2 的对称修复 |
| 4 | `views/chat.py:834` | `st.data_editor(df, key="edit_df")` 使用固定 key，切换 Excel 工作表后 widget 状态跨表泄漏 → **跨表数据污染**。 | key 绑定到当前表名 `key=f"edit_df_{doc.active_sheet}"` |

## 🟡  suggestions（已修复，真实但非致命）

| # | 文件:行 | 问题 | 修复 |
|---|---------|------|------|
| 5 | `editing/document_model.py:62` | `pd.ExcelFile` 未关闭，Windows 下泄漏文件句柄，后续写回同路径会 `PermissionError`。 | 改为 `with pd.ExcelFile(self.path) as xls:` |
| 6 | `editing/edit_interpreter.py:288-289` | `find`/`replace` 仅 `.strip()`，带引号说法（如 `把'旧'替换成'新'`）无法匹配未加引号的单元格 → 编辑静默无效果。 | 增加 `.strip("'\"")` 与 `set_cell` 的 `_coerce_value` 行为对齐 |
| 7 | `artifacts/coordinator.py:701,779` | 模板计划解析被 `except Exception: pass` 吞掉，真实 bug（KeyError/TypeError）被静默降级为纯提示词生成。 | 收窄为 `(ValidationError, json.JSONDecodeError, ValueError, TypeError)` 并 `logging.warning` 暴露失败 |
| 8 | `providers/gateway.py:532,565` | 流式路径 `requires_vision = bool(task_type == "vision")` 几乎恒为 False（且 `stream_chat` 不接收图片），导致带图请求走流式时被静默丢弃。 | 新增 `image_paths` 参数；`requires_vision = bool(image_paths)`；客户端支持时转发（`getattr(client,"stream_chat_with_images",None)`），否则降级不崩溃 |
| 9 | `ui_helpers.py:1324` | `@st.cache_data` 内部读取 `st.session_state`/`get_workflow_coordinator()`，缓存为全局且与 agent/coordinator 实例绑定，跨会话/实例返回陈旧预览，且可能在脚本运行外抛 `StreamlitAPIException`。 | 移除 `@st.cache_data`（仅 5 条待审批运行，重算开销可忽略） |

## 💭  nits（已修复/备注）

| # | 文件:行 | 问题 | 处理 |
|---|---------|------|------|
| 10 | `routing/service.py:474` | `response.strip().strip('"\'') .split()[0]` 在响应仅为引号/空白时 `[0]` 抛 `IndexError`（虽被外层 except 兜住，但脆弱）。 | 改为先判空再取 `parts[0]`，无内容返回 `None` |

## 🧹  清理项

- **隔离陈旧备份**：历史版本曾包含 `artpm_agent/app.py.wip_bak`；该文件已从当前仓库移除，模块化 `artpm_agent/app.py` 是现行入口。

## ✅  验证为干净的项

- 无 `eval`/`exec`、无 `subprocess shell=True`、无裸 `except:`、无可变默认参数。
- SQL：仅 `core/token_monitor.py` 用 f-string 插值**日期**（`datetime.now()`，非用户输入），无可利用注入；其余均参数化或使用 ORM。
- `artifacts/generator.py` 的 `_finalize`/`store_versioned_bytes` 返回契约（`name`/`version`）与调用方一致；临时文件均在 `finally` 中清理。
- `runtime/agent_loop.py`、`editing/feedback_store.py`、`editing/reflection.py`、`skills/skill_router.py`、`database/models.py` 逻辑健全。

## ⚠️  子代理误报（已核实，未改动）

- 子代理曾报告 `editing/rule_distiller.py:74` “空 `to` 删除文本”（`_apply_normalize_rules`）。经核实该代码在仓库中**不存在**（实际 line 74 为 `_now()`），属误报，未做改动。

## 🔎  验证方式

- 全部 10 个改动文件 `python -m py_compile` 通过（`ALL_COMPILE_OK`）。
- 网关流式块缩进与 `chunks` 变量使用经复查正确。

## 📌  后续可选优化（本次未做，供参考）

- `memory/workspace_knowledge_store.py:1989`：`search()` 硬编码 `LIMIT 2000` 在超大知识库下会静默丢弃结果，建议把 `resource_type`/过滤与分页下推到 SQL。
- `editing/edit_interpreter.py:526`：`_call_llm_fallback` 的 `except Exception` 刻意不崩 UI，但会掩盖接线类 bug，建议改为仅捕获预期异常并 `logging.exception`。
- `artifacts/generator.py`：`FileExistsError("No free artifact version found")` 建议补上 `filename` 便于诊断。
