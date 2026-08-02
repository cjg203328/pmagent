"""Natural-language -> edit-operation interpreter.

Offline-first: a deterministic grammar covers the common table/Word edit verbs
in Chinese. An optional ``rule_store`` (learned from past corrections, see
``rule_distiller``) is tried next - still free, no API key. Only when neither
the grammar nor a learned rule matches AND an ``llm_callable`` is supplied do we
ask the LLM to parse the *remaining* sub-clauses, so ambiguous phrasings still
get understood without paying for the parts the rule engine already handles.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_CN_DIGIT = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

# Conjunctions / separators that split one instruction into several edits.
_COMPOUND_SPLIT = re.compile(
    r"(?:并且|而且|同时|然后|接着|顺便|另外|以及|并且也|并|[，,；;。])"
)


def _cn_to_int(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGIT.get(left, 1) if left else 1
        ones = _CN_DIGIT.get(right, 0) if right else 0
        return tens * 10 + ones
    if text in _CN_DIGIT:
        return _CN_DIGIT[text]
    return None


def _coerce_value(raw: str) -> Any:
    raw = raw.strip().strip("'\"")
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _resolve_column(df: Any, name: str) -> str | None:
    name = name.strip()
    cols = [str(c) for c in df.columns]
    if name in cols:
        return name
    # fuzzy contains (first match)
    for c in cols:
        if name in c or c in name:
            return c
    return None


def _row_index_from(text: str) -> int | None:
    m = re.search(r"第\s*([0-9一二三四五六七八九十]+)\s*行?", text)
    if m:
        return _cn_to_int(m.group(1))
    m = re.search(r"^([0-9一二三四五六七八九十]+)\s*行", text)
    if m:
        return _cn_to_int(m.group(1))
    return None


def _cell_criteria(text: str) -> dict[str, Any] | None:
    text = text.strip().strip("的").strip()
    # index form
    idx = _row_index_from(text)
    if idx is not None:
        return {"type": "index", "value": idx}
    # keyword forms: 包含X / 名为X / 叫X / 是X
    m = re.search(r"(包含|含|名为|叫|是|等于|为)\s*(.+)", text)
    if m:
        key, target = m.group(1), m.group(2).strip()
        mode = "equals" if key in {"名为", "叫", "是", "等于", "为"} else "contains"
        return {"type": "cell", "match": target, "mode": mode}
    if text:
        return {"type": "cell", "match": text, "mode": "contains"}
    return None


def _find_rows(df: Any, column: str | None, criteria: dict[str, Any]) -> list[int]:
    if criteria.get("type") == "index":
        idx = criteria["value"]
        return [idx - 1] if 1 <= idx <= len(df) else []
    target = str(criteria.get("match", ""))
    mode = criteria.get("mode", "contains")
    cols = [column] if column and column in df.columns else list(df.columns)
    matched: list[int] = []
    for i in range(len(df)):
        for col in cols:
            val = df.iloc[i][col]
            if val is None:
                continue
            sval = str(val)
            if mode == "equals" and sval == target:
                matched.append(i)
                break
            if mode == "contains" and target in sval:
                matched.append(i)
                break
    # de-dup preserving order
    seen: set[int] = set()
    result: list[int] = []
    for i in matched:
        if i not in seen:
            seen.add(i)
            result.append(i)
    return result


def _split_compound(instr: str) -> list[str]:
    """Split a compound instruction into individual edit clauses."""
    parts = _COMPOUND_SPLIT.split(instr)
    return [p.strip() for p in parts if p and p.strip()]


# ---- LLM fallback ---------------------------------------------------
# Declarative schema of every edit op the engine can apply. The LLM is asked to
# emit ops matching this schema; we validate strictly before trusting them so a
# confused model can never corrupt the document.
_OP_SCHEMAS: dict[str, dict] = {
    "set_cell": {"kind": "excel", "required": ["sheet", "column", "criteria", "value"]},
    "replace_in_column": {"kind": "excel", "required": ["sheet", "column", "find", "replace"]},
    "delete_row": {"kind": "excel", "required": ["sheet", "criteria"]},
    "add_row": {"kind": "excel", "required": ["sheet"]},
    "sort": {"kind": "excel", "required": ["sheet", "column"]},
    "text_replace": {"kind": "word", "required": ["find", "replace"]},
    "delete_block": {"kind": "word", "required": ["criteria"]},
    "add_block": {"kind": "word", "required": ["block"]},
}

LLM_EDIT_SYSTEM_PROMPT = (
    "你是一个表格/文档编辑指令解析器。用户用自然语言描述一次或多次编辑，"
    "你必须把意图翻译成严格符合给定 schema 的 JSON 操作列表。"
    "只输出 JSON，不要解释、不要 markdown 代码块。格式："
    '{"ops":[...], "warnings":[...]}。'
    "criteria 用 {\"match\":\"值\", \"mode\":\"contains|equals\"} 或 "
    "{\"type\":\"index\", \"value\":N}（第 N 行，从 1 开始）。"
    "value 保持原始类型（数字不要加引号）。不要臆造文档里不存在的列名或工作表名。"
    "若用户一次要求多个改动，请在 ops 中输出多个操作对象（复合指令）。"
)

# An illustrative compound example embedded into the prompt so the model knows
# to emit several ops from one sentence.
_LLM_COMPOUND_EXAMPLE = (
    "示例 - 复合指令：\"把角色A的单价改成5000，并删除第3行\"\n"
    '→ {"ops":['
    '{"op":"set_cell","sheet":"报价","column":"单价","criteria":{"match":"角色A","mode":"contains"},"value":5000},'
    '{"op":"delete_row","sheet":"报价","criteria":{"type":"index","value":3}}]}'
)


def interpret(
    instruction: str,
    doc: Any,
    llm_callable: Any = None,
    rule_store: Any = None,
) -> dict[str, Any]:
    """Return ``{"ops", "notes", "warnings", "source"}``.

    Resolution order (cheapest first):
      1. Offline regex grammar (free + deterministic).
      2. Learned rules from past corrections (free, ``rule_store``).
      3. LLM fallback - but only for the sub-clauses the above two missed.

    ``source`` is one of ``"regex"`` / ``"rule"`` / ``"llm"`` or a ``"+"``
    joined combination when several contributed (e.g. ``"regex+llm"``).
    """
    instr = (instruction or "").strip()
    result: dict[str, Any] = {"ops": [], "notes": [], "warnings": [], "source": "none"}
    if not instr:
        result["warnings"].append("指令为空")
        return result

    # 0) Learned normalization rules rewrite口语说法 -> 标准说法 (always free)
    if rule_store is not None:
        instr = _apply_normalize_rules(instr, doc.kind, rule_store)

    sources: set[str] = set()

    # 1) Offline regex grammar, clause by clause (supports compound instructions)
    clauses = _split_compound(instr)
    unparsed: list[str] = []
    for clause in clauses:
        matched = (
            _interpret_excel_clause(clause, doc, result)
            if doc.kind == "excel"
            else _interpret_word_clause(clause, result)
        )
        if not matched:
            unparsed.append(clause)
    if result["ops"]:
        sources.add("regex")

    # 2) Learned ops rules (free, distilled from past 👎 corrections)
    if rule_store is not None:
        hit = _apply_learned_ops_rules(instr, doc, result, rule_store)
        if hit:
            sources.add("rule")

    # 3) LLM fallback - only for what grammar + rules could NOT cover
    if (unparsed or not result["ops"]) and llm_callable is not None:
        target = "；".join(unparsed) if unparsed else instr
        llm_result = _call_llm_fallback(target, doc, llm_callable)
        if llm_result["ops"]:
            sources.add("llm")
        result["ops"].extend(llm_result["ops"])
        result["notes"].extend(llm_result["notes"])
        result["warnings"].extend(llm_result["warnings"])

    if not result["ops"]:
        result["warnings"].append(
            "未能理解该指令，请换种说法（例如：把角色A的单价改成5000 / 删除第3行 / 在末尾加一段：...）"
        )
    result["source"] = "+".join(sorted(sources)) if sources else "none"
    return result


def _interpret_excel(instr: str, doc: Any, result: dict[str, Any]) -> list[str]:
    """Back-compat shim: run clause interpreter and return unparsed clauses."""
    unparsed: list[str] = []
    for clause in _split_compound(instr):
        if not _interpret_excel_clause(clause, doc, result):
            unparsed.append(clause)
    return unparsed


def _interpret_excel_clause(clause: str, doc: Any, result: dict[str, Any]) -> bool:
    """Try each Excel edit pattern on *one* clause. Append the first match and
    return ``True``; return ``False`` (unparsed -> LLM) otherwise. A pattern that
    matches structurally but references a non-existent column counts as
    unparsed so the LLM can attempt it."""
    sheet = doc.active_sheet
    df = doc.active_df

    # 1) set cell: 把/将/让 <criteria> 的 <col> 改成/设为/更新为/.../调成 <value>
    m = re.search(
        r"(?:把|将|让)\s*(.+?)\s*的\s*([^\s，,。]+?)\s*"
        r"(?:改成|设为|更新为|改为|等于|置为|调整为|调成|调为)\s*(.+)",
        clause,
    )
    if m:
        criteria_text, col_text, value_text = m.group(1), m.group(2), m.group(3)
        column = _resolve_column(df, col_text)
        if column is None:
            result["warnings"].append(f"未找到列「{col_text}」")
            return False
        criteria = _cell_criteria(criteria_text) or {
            "type": "cell", "match": criteria_text, "mode": "contains"
        }
        result["ops"].append({
            "op": "set_cell",
            "sheet": sheet,
            "criteria": criteria,
            "column": column,
            "value": _coerce_value(value_text),
        })
        result["notes"].append(
            f"将列「{column}」中匹配「{criteria.get('match')}」的单元格设为 {value_text}"
        )
        return True

    # 2) replace within a column: 把<col>里/中/列 的 <X> 替换成/改为/调成 <Y>
    m = re.search(
        r"(?:把|将)\s*([^\s，,。]+?)\s*(?:里|中|列)\s*的\s*(.+?)\s*"
        r"(?:替换成|替换为|改成|改为|调成|调为)\s*(.+)",
        clause,
    )
    if m:
        col_text, find, replace = m.group(1), m.group(2), m.group(3)
        column = _resolve_column(df, col_text)
        if column is None:
            result["warnings"].append(f"未找到列「{col_text}」")
            return False
        result["ops"].append({
            "op": "replace_in_column",
            "sheet": sheet,
            "column": column,
            "find": find.strip().strip("'\""),
            "replace": replace.strip().strip("'\""),
        })
        result["notes"].append(f"在列「{column}」中将「{find}」替换为「{replace}」")
        return True

    # 3) delete row: 删除 <criteria> 行 / 第N行 / 包含X的行
    m = re.search(r"删除\s*(.+?)\s*(?:的行)?$", clause)
    if m and "删除" in clause:
        target = m.group(1)
        criteria = _cell_criteria(target) or {
            "type": "cell", "match": target, "mode": "contains"
        }
        result["ops"].append({"op": "delete_row", "sheet": sheet, "criteria": criteria})
        if criteria.get("type") == "index":
            result["notes"].append(f"删除第 {criteria['value']} 行")
        else:
            result["notes"].append(f"删除匹配「{criteria.get('match')}」的行")
        return True

    # 4) add row: 加一行 / 添加一行 / 新增一行 (+ 值)
    if re.search(r"(?:加|添加|新增|插入)\s*一?\s*行", clause):
        values: Any = {}
        mv = re.search(r"[：:]\s*(.+)", clause)
        if mv:
            parts = [p.strip() for p in re.split(r"[，,、]", mv.group(1)) if p.strip()]
            has_kv = any(("为" in p or "：" in p or ":" in p) for p in parts)
            if has_kv:
                values = {}
                for part in parts:
                    if "为" in part or "：" in part or ":" in part:
                        k, _, v = re.split(r"(?:为|：|:)", part, maxsplit=1)
                        values[k.strip()] = _coerce_value(v)
            else:
                values = [_coerce_value(p) for p in parts]  # positional
        after = _row_index_from(clause)
        result["ops"].append({
            "op": "add_row",
            "sheet": sheet,
            "after": after,
            "values": values,
        })
        result["notes"].append("新增一行" + (f"，填入：{values}" if values else ""))
        return True

    # 5) sort: 按<col>排序 (升序/降序)
    m = re.search(r"按\s*([^\s，,。]+?)\s*(?:排|顺)序?\s*(升|降|倒)?", clause)
    if m and "排序" in clause:
        col_text = m.group(1)
        column = _resolve_column(df, col_text)
        if column is None:
            result["warnings"].append(f"未找到列「{col_text}」")
            return False
        ascending = m.group(2) != "降" and m.group(2) != "倒"
        result["ops"].append({
            "op": "sort",
            "sheet": sheet,
            "column": column,
            "ascending": ascending,
        })
        result["notes"].append(f"按「{column}」{'升' if ascending else '降'}序排序")
        return True

    return False


def _interpret_word(instr: str, result: dict[str, Any]) -> list[str]:
    """Back-compat shim: run clause interpreter and return unparsed clauses."""
    unparsed: list[str] = []
    for clause in _split_compound(instr):
        if not _interpret_word_clause(clause, result):
            unparsed.append(clause)
    return unparsed


def _interpret_word_clause(clause: str, result: dict[str, Any]) -> bool:
    """Try each Word edit pattern on one clause; append first match, return True."""
    # 1) text replace across document
    m = re.search(
        r"(?:把|将)\s*(.+?)\s*(?:替换成|替换为|改成|改为|调成|调为)\s*(.+)", clause
    )
    if m and ("替换" in clause or "改成" in clause or "改为" in clause or "调成" in clause or "调为" in clause):
        find, replace = m.group(1).strip(), m.group(2).strip()
        result["ops"].append({"op": "text_replace", "find": find, "replace": replace})
        result["notes"].append(f"全文将「{find}」替换为「{replace}」")
        return True

    # 2) delete paragraph containing X
    m = re.search(r"删除\s*(?:包含|名为)?\s*(.+?)\s*的?\s*(?:段落|段)$", clause)
    if m and "删除" in clause:
        target = m.group(1).strip()
        result["ops"].append({
            "op": "delete_block",
            "criteria": {"contains": target, "mode": "contains"},
        })
        result["notes"].append(f"删除包含「{target}」的段落")
        return True

    # 3) add heading / paragraph at end
    m = re.search(
        r"(?:在末尾|最后)?\s*(?:加|添加|新增|插入)\s*(一段|一个段落|标题|一段文字)[:：]?\s*(.+)",
        clause,
    )
    if m and ("加" in clause or "添加" in clause or "新增" in clause):
        text = m.group(2).strip()
        kind = "heading" if "标题" in clause else "paragraph"
        level = 1
        if "二级" in clause:
            level = 2
        elif "三级" in clause:
            level = 3
        result["ops"].append({
            "op": "add_block",
            "block": {"type": kind, "text": text, "level": level},
        })
        result["notes"].append(f"在文末添加{kind}：{text}")
        return True

    return False


# ---- Learned-rule injection (free, distilled from past corrections) -----
def _apply_normalize_rules(instr: str, doc_type: str, rule_store: Any) -> str:
    """Rewrite口语 tokens into standard tokens using learned normalize rules."""
    for rule in rule_store.normalize_rules(doc_type):
        frm = rule.get("from", "")
        to = rule.get("to", "")
        if frm and to != frm and frm in instr:
            instr = instr.replace(frm, to)
    return instr


def _apply_learned_ops_rules(
    instr: str, doc: Any, result: dict[str, Any], rule_store: Any
) -> bool:
    """If a learned ops-rule triggers on *instr*, append its ops (free)."""
    matched = rule_store.match(instr, doc.kind)
    if not matched:
        return False
    ops, notes = matched
    result["ops"].extend(ops)
    result["notes"].extend(notes)
    return True


# ---- LLM fallback helpers ------------------------------------------
def _doc_schema(doc: Any) -> str:
    """A compact, LLM-readable description of the document structure."""
    if doc.kind == "excel":
        parts = []
        for name, df in doc.sheets.items():
            cols = ", ".join(str(c) for c in df.columns)
            parts.append(f"工作表「{name}」列: [{cols}]，共 {len(df)} 行")
        return "Excel 文档。\n" + "\n".join(parts) + f"\n当前活动表: {doc.active_sheet}"
    blocks = "; ".join(
        f"{b.get('type')}: {b.get('text', '')[:20]}" for b in doc.blocks[:15]
    )
    return f"Word 文档，共 {len(doc.blocks)} 个区块。示例: {blocks}"


def _build_llm_prompt(instruction: str, doc: Any) -> str:
    schema_lines = [
        f"- {op} (作用于{'Excel' if spec['kind'] == 'excel' else 'Word'}，"
        f"必填: {', '.join(spec['required'])})"
        for op, spec in _OP_SCHEMAS.items()
    ]
    schema = "\n".join(schema_lines)
    return (
        f"文档结构:\n{_doc_schema(doc)}\n\n"
        f"可执行操作 schema:\n{schema}\n\n"
        f"{_LLM_COMPOUND_EXAMPLE}\n\n"
        f"用户指令: {instruction}\n\n"
        "请输出符合 schema 的 JSON 操作列表（复合指令请输出多个 op）。"
    )


def _parse_llm_plan(raw: str) -> tuple[list, list]:
    """Best-effort extraction of an ``{"ops":[...]}`` plan from LLM text."""
    if not raw:
        return [], ["AI 返回为空"]
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return [], ["AI 返回无法解析为 JSON"]
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        return [], [f"AI 返回 JSON 解析失败: {exc}"]
    ops = data.get("ops", []) if isinstance(data, dict) else []
    if not isinstance(ops, list):
        ops = []
    return ops, []


def _validate_llm_ops(ops: list, doc: Any) -> tuple[list, list]:
    """Keep only ops that match the schema and the actual document."""
    valid: list = []
    warnings: list = []
    excel_cols = {
        name: list(df.columns) for name, df in doc.sheets.items()
    } if doc.kind == "excel" else {}
    for op in ops:
        if not isinstance(op, dict) or "op" not in op:
            warnings.append("丢弃了无效的 op（缺少 op 字段）")
            continue
        name = op["op"]
        spec = _OP_SCHEMAS.get(name)
        if spec is None:
            warnings.append(f"不支持的操作类型: {name}")
            continue
        if spec["kind"] != doc.kind:
            warnings.append(f"操作 {name} 不适用于{doc.kind}文档")
            continue
        missing = [f for f in spec["required"] if f not in op]
        if missing:
            warnings.append(f"操作 {name} 缺少字段: {', '.join(missing)}")
            continue
        if doc.kind == "excel":
            sheet = op.get("sheet")
            if sheet not in doc.sheets:
                warnings.append(f"工作表不存在: {sheet}")
                continue
            if "column" in op and op["column"] not in excel_cols.get(sheet, []):
                warnings.append(f"列不存在: {op['column']}")
                continue
            if name == "set_cell" and isinstance(op.get("value"), str):
                op["value"] = _coerce_value(op["value"])
        valid.append(op)
    return valid, warnings


def _call_llm_fallback(instruction: str, doc: Any, llm_callable: Any) -> dict:
    prompt = _build_llm_prompt(instruction, doc)
    try:
        raw = llm_callable(prompt)
    except Exception as exc:  # noqa: BLE001 - never let the LLM crash the UI
        logging.getLogger(__name__).exception("LLM 兜底解析调用失败")
        return {"ops": [], "notes": [], "warnings": [f"AI 解析调用失败: {exc}"]}
    ops, parse_warn = _parse_llm_plan(raw)
    valid_ops, val_warn = _validate_llm_ops(ops, doc)
    return {
        "ops": valid_ops,
        "notes": [f"AI 解析指令：{instruction}"],
        "warnings": parse_warn + val_warn,
    }


def make_llm_callable(llm_client: Any, system_prompt: str | None = None) -> Any:
    """Wrap an ``BaseLLMClient`` (``chat(prompt, system_prompt=)``) into the
    ``llm_callable(prompt) -> str`` contract used by ``interpret``.

    Returns ``None`` when *llm_client* is ``None`` so callers can forward it
    safely (regex-only mode, no API key configured).
    """
    if llm_client is None:
        return None
    sp = system_prompt or LLM_EDIT_SYSTEM_PROMPT

    def _call(prompt: str) -> str:
        return llm_client.chat(prompt, system_prompt=sp)

    return _call
