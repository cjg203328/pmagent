# 代码审查 · 后续可选优化（已处理）

对全量审查中标记为「后续可选优化」的三项进行修复。改动文件均通过 `py_compile` 验证。

## 🟡 `memory/workspace_knowledge_store.py` — 搜索 LIMIT 2000 静默截断
- **位置**: `search()` 内 SQL（原 line 1977–1994）
- **问题**: 资源查询 `LIMIT 2000` 在应用 `resource_type` / `source_type` 过滤**之前**截断。当工作区活跃资源 > 2000 且所请求类型偏旧（按 `updated_at DESC` 排序落在 2000 之外）时，相关资源被静默丢弃，向量与字面检索都拿不到 —— 即请求了某种类型却永远搜不到。
- **修复**: 把 `resource_type` / `source_type` 过滤下推到 SQL（`IN (?, ?)` 参数化），`LIMIT 2000` 改为在「已过滤」结果上生效。过滤为空时行为与原先完全一致（不追加 WHERE 子句）。Python 侧的 `eligible_rows` 二次过滤保留为安全网，结果不变。
- **影响**: 大资源量工作区按类型检索不再漏结果；参数化，无注入风险。

## 🟡 `editing/edit_interpreter.py` — LLM 兜底吞错
- **位置**: `_call_llm_fallback()` 的 `except Exception`（line ~526）
- **问题**: 原本只把异常塞进一条 warning 返回，真实编程错误（如 `llm_callable` 签名不符、`TypeError`）被一并掩盖，排障时无迹可寻。
- **修复**: 在返回安全兜底结果**之前**加 `logging.getLogger(__name__).exception(...)`，把完整堆栈写入日志。UI 仍不崩溃（兜底逻辑保留），但故障在日志中可见。
- **新增**: `import logging`。

## 💭 `artifacts/generator.py` — FileExistsError 诊断信息
- **位置**: `WorkspaceArtifactGenerator._publish_new()`（line 190）
- **问题**: 找不到可用版本槽位时抛 `FileExistsError("No free artifact version found after N attempts")`，缺文件名，排障时无法定位是哪个产物卡死。
- **修复**: 消息加入 `{filename!r}` —— `No free artifact version found for '<name>' after N attempts`。

## 🟡 `memory/workspace_knowledge_store.py` — 已采纳规则 LIMIT 1000 静默截断
- **位置**: `search()` 内规则查询（原 line 2005–2007）
- **问题**: 与前述 `LIMIT 2000` 同源 —— 规则查询 `ORDER BY updated_at DESC LIMIT 1000` 在**字面查询匹配之前**截断。资源侧还有 `eligible_rows` 做 Python 二次过滤当安全网，但规则侧**没有**任何安全网：下游（line 2131）只对 `rule_rows` 里 `_literal_score(query, statement) > 0` 的规则保留。当某工作区已采纳规则 > 1000、且某条相关规则较旧（按 `updated_at` 排在第 1000 之外）时，它会被静默丢弃，导致「这条规则明明存在却搜不到」。
- **修复**: 把字面查询匹配下推到 SQL —— 用查询词（整句 + 分词，与 `_literal_score` 的 token 化一致）构造 `statement LIKE ? ESCAPE ?` 的 OR 子句，先框定「相关」候选集，再套 `LIMIT 1000`。这样 `LIMIT` 只作用于已相关行，旧但相关的规则不再被漏掉。
  - LIKE 特殊字符 `%` / `_` 及转义符本身均做了转义（`ESCAPE '\'`），避免查询含这些字符时语义被改变。
  - 参数化，无注入风险；`query` 经 `_required_text` 保证非空，额外 `if like_terms` 守卫纯属防御。
- **残留 caveat（💭）**: 若查询是极常见的单字（如「的」）且匹配语句数 > 1000，仍会触发该上限；但此时均为低相关度匹配，最终 `results[:limit]` 排序截断已能消化，影响可忽略。

## 验证
- `py_compile` 全部通过：`workspace_knowledge_store.py`、`edit_interpreter.py`、`generator.py`
- 本次追加改动另过 `py_compile`：`workspace_knowledge_store.py`（规则查询重构）
- 未改动任何对外契约；`search()` 的返回结构 / `_publish_new` 的返回签名 / `_call_llm_fallback` 的返回 dict 均保持不变。
