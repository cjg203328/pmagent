"""Distill one-sentence-edit corrections into *free* reusable rules.

This is what lets high-frequency phrasings "graduate" from the paid LLM path
back to the free rule engine:

  * ``normalize`` rules rewrite a colloquial token into a standard one
    (e.g. "调成" -> "改成") so the offline regex can parse it - zero cost.
  * ``ops`` rules bind a trigger keyword phrase directly to concrete edit
    operations, so a familiar instruction skips the LLM entirely.

Rules are mined from the 👎 / "我手动改了" feedback captured by
``EditFeedbackStore``. When an LLM callable is available the miner asks the
model to produce a clean structural rule; otherwise it falls back to an
offline synonym-diff so even key-less installs keep learning.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Re-used op vocabulary so distilled ``ops`` rules stay schema-valid.
_KNOWN_OPS = {
    "set_cell", "replace_in_column", "delete_row",
    "add_row", "sort", "text_replace", "delete_block", "add_block",
}
_OP_KIND = {
    "set_cell": "excel", "replace_in_column": "excel", "delete_row": "excel",
    "add_row": "excel", "sort": "excel",
    "text_replace": "word", "delete_block": "word", "add_block": "word",
}

_LLM_DISTILL_PROMPT = (
    "你负责把用户的一次\"纠正\"提炼成一条可复用的编辑规则。\n"
    "原指令(用户实际说的，但系统没理解对): {instr}\n"
    "纠正说明(用户给出的正确做法): {corr}\n"
    "文档类型: {dtype}\n"
    "{doc_schema}"
    "请只输出一个 JSON 规则对象，二选一：\n"
    "1) 归一化规则(把口语化说法转成标准说法，让正则能识别):\n"
    '   {"kind":"normalize","from":"<原话里的词>","to":"<标准词>","doc_type":"<dtype>"}\n'
    "2) 直接操作规则(当无法用简单归一化表达时，给出具体编辑):\n"
    '   {"kind":"ops","trigger":"<未来命中该规则的关键短语>",'
    '"doc_type":"<dtype>","match_mode":"contains",'
    '"ops":[<与 edit schema 一致的 op 列表，sheet/列名须来自上面的文档结构>]}\n'
    "只输出 JSON，不要解释。"
)


def _build_distill_prompt(instr: str, corr: str, dtype: str, doc_schema: str) -> str:
    """Render the distill prompt without .format() (the template contains JSON
    braces, so we substitute only the dynamic fields explicitly)."""
    return (
        "你负责把用户的一次\"纠正\"提炼成一条可复用的编辑规则。\n"
        f"原指令(用户实际说的，但系统没理解对): {instr}\n"
        f"纠正说明(用户给出的正确做法): {corr}\n"
        f"文档类型: {dtype}\n"
        f"{doc_schema}"
        "请只输出一个 JSON 规则对象，二选一：\n"
        "1) 归一化规则(把口语化说法转成标准说法，让正则能识别):\n"
        '   {"kind":"normalize","from":"<原话里的词>","to":"<标准词>","doc_type":"<dtype>"}\n'
        "2) 直接操作规则(当无法用简单归一化表达时，给出具体编辑):\n"
        '   {"kind":"ops","trigger":"<未来命中该规则的关键短语>",'
        '"doc_type":"<dtype>","match_mode":"contains",'
        '"ops":[<与 edit schema 一致的 op 列表，sheet/列名须来自上面的文档结构>]}\n'
        "只输出 JSON，不要解释。"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ops_hash(ops: Any) -> str:
    try:
        return json.dumps(ops, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(ops)


def _diff_normalize(instr: str, corr: str) -> tuple[str, str] | None:
    """If *corr* equals *instr* with one contiguous substring replaced, return
    ``(bad, good)``; otherwise ``None``. Used for offline synonym learning."""
    instr, corr = instr.strip(), corr.strip()
    if not instr or not corr or instr == corr:
        return None
    # common prefix
    i = 0
    while i < len(instr) and i < len(corr) and instr[i] == corr[i]:
        i += 1
    # common suffix
    j = 0
    while (
        j < len(instr) - i
        and j < len(corr) - i
        and instr[-1 - j] == corr[-1 - j]
    ):
        j += 1
    bad = instr[i : len(instr) - j]
    good = corr[i : len(corr) - j]
    if not bad or not good or bad == good:
        return None
    # guard: keep tokens short and recoverable
    if len(bad) > 6 or len(good) > 6:
        return None
    # make sure the swap actually reconstructs corr
    if instr[:i] + good + instr[len(instr) - j :] != corr:
        return None
    return bad, good


class RuleDistiller:
    """Manages the persisted set of learned edit rules (``learned_rules.json``)."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = Path(__file__).resolve().parent / "learned_rules.json"
        self.path = Path(path)
        self._rules: list[dict[str, Any]] | None = None

    # ---- persistence ----
    def _load(self) -> None:
        if self._rules is not None:
            return
        if self.path.exists():
            try:
                self._rules = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._rules = []
        else:
            self._rules = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._rules, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- queries ----
    def all(self) -> list[dict[str, Any]]:
        self._load()
        return list(self._rules)

    def count(self) -> int:
        self._load()
        return len(self._rules)

    def normalize_rules(self, doc_type: str) -> list[dict[str, Any]]:
        self._load()
        return [
            r
            for r in self._rules
            if r.get("kind") == "normalize"
            and r.get("doc_type") in (doc_type, "any")
        ]

    def match(self, instruction: str, doc_type: str) -> tuple[list, list] | None:
        """Return ``(ops, notes)`` for the first ops-rule that triggers, else None."""
        self._load()
        instr = (instruction or "").strip()
        for r in self._rules:
            if r.get("kind") != "ops":
                continue
            if r.get("doc_type") not in (doc_type, "any"):
                continue
            trig = r.get("trigger", "")
            mode = r.get("match_mode", "contains")
            ok = False
            if trig:
                if mode == "regex":
                    try:
                        ok = re.search(trig, instr) is not None
                    except re.error:
                        ok = False
                else:
                    ok = trig in instr
            if ok:
                return r.get("ops", []), [f"命中已学规则：{trig}"]
        return None

    # ---- mutation ----
    def add(self, rule: dict[str, Any]) -> bool:
        self._load()
        key = (
            rule.get("doc_type"),
            rule.get("kind"),
            rule.get("from") or rule.get("trigger"),
            _ops_hash(rule.get("ops")),
        )
        for existing in self._rules:
            ekey = (
                existing.get("doc_type"),
                existing.get("kind"),
                existing.get("from") or existing.get("trigger"),
                _ops_hash(existing.get("ops")),
            )
            if ekey == key:
                return False
        self._rules.append(rule)
        self._save()
        return True

    def clear(self) -> None:
        self._rules = []
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass


def distill_from_correction(
    store: Any,
    llm_callable: Any = None,
    distiller: RuleDistiller | None = None,
    doc: Any = None,
) -> int:
    """Mine rejected feedback (with a correction) into learned rules.

    Returns the number of *new* rules added. When ``llm_callable`` is supplied
    the miner asks the model for a structural rule (best quality); otherwise it
    falls back to an offline synonym diff so learning still happens offline.
    """
    if distiller is None:
        distiller = RuleDistiller()
    rows = [
        r
        for r in store.all()
        if (not r.get("accepted")) and r.get("correction")
    ]
    new_count = 0
    for r in rows:
        instr = r.get("instruction", "")
        corr = (r.get("correction") or "").strip()
        dtype = r.get("doc_type", "any")
        if not corr:
            continue
        if llm_callable is not None:
            rule = _llm_distill_rule(instr, corr, dtype, llm_callable, doc)
            if rule and distiller.add(rule):
                new_count += 1
        else:
            norm = _diff_normalize(instr, corr)
            if norm and distiller.add(_norm_rule(norm, dtype, instr, corr)):
                new_count += 1
    return new_count


def _norm_rule(
    norm: tuple[str, str], dtype: str, instr: str, corr: str
) -> dict[str, Any]:
    bad, good = norm
    return {
        "kind": "normalize",
        "doc_type": dtype,
        "from": bad,
        "to": good,
        "created": _now(),
        "source_instruction": instr,
        "source_correction": corr,
        "hits": 0,
    }


def _llm_distill_rule(
    instr: str, corr: str, dtype: str, llm_callable: Any, doc: Any
) -> dict[str, Any] | None:
    doc_schema = ""
    if doc is not None:
        try:
            from artpm_agent.editing.edit_interpreter import _doc_schema
            doc_schema = "文档结构:\n" + _doc_schema(doc) + "\n"
        except Exception:  # noqa: BLE001
            doc_schema = ""
    prompt = _build_distill_prompt(instr, corr, dtype, doc_schema)
    try:
        raw = llm_callable(prompt)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "kind" not in data:
        return None

    if data["kind"] == "normalize":
        frm = data.get("from", "")
        to = data.get("to", "")
        if not frm or not to or frm == to:
            return None
        return _norm_rule((frm, to), dtype, instr, corr)

    if data["kind"] == "ops":
        ops = data.get("ops")
        trig = data.get("trigger", "")
        if not isinstance(ops, list) or not ops or not trig:
            return None
        valid_ops = []
        for op in ops:
            if not isinstance(op, dict) or op.get("op") not in _KNOWN_OPS:
                continue
            if doc is not None and _OP_KIND.get(op["op"]) != doc.kind:
                continue
            valid_ops.append(op)
        if not valid_ops:
            return None
        return {
            "kind": "ops",
            "doc_type": dtype,
            "trigger": trig,
            "match_mode": data.get("match_mode", "contains"),
            "ops": valid_ops,
            "created": _now(),
            "source_instruction": instr,
            "source_correction": corr,
            "hits": 0,
        }
    return None
