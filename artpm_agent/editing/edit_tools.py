"""Apply declarative edit operations to an EditableDocument in memory."""

from __future__ import annotations

import copy
from typing import Any

import pandas as pd


def apply_plan(doc: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Mutate *doc* according to ``plan["ops"]`` and return a result summary.

    Returns ``{"applied", "errors", "changes", "ops", "warnings"}`` where
    ``changes`` is the human-readable diff vs the document state before.
    ``ops`` / ``warnings`` are echoed from the plan so callers (e.g. the chat
    UI) can branch on whether anything was understood.
    """
    ops = list(plan.get("ops", []))
    before = copy.deepcopy(doc)
    applied: list[str] = []
    errors: list[str] = []
    for op in ops:
        try:
            _apply_one(doc, op, applied)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the UI
            errors.append(f"{op.get('op')}: {exc}")
    changes = doc.diff_from(before)
    return {
        "applied": applied,
        "errors": errors,
        "changes": changes,
        "ops": ops,
        "warnings": list(plan.get("warnings", [])),
    }


def _apply_one(doc: Any, op: dict[str, Any], applied: list[str]) -> None:
    handler = {
        "set_cell": _set_cell,
        "replace_in_column": _replace_in_column,
        "delete_row": _delete_row,
        "add_row": _add_row,
        "sort": _sort,
        "text_replace": _text_replace,
        "delete_block": _delete_block,
        "add_block": _add_block,
    }.get(op.get("op"))
    if handler is None:
        raise ValueError(f"unknown op: {op.get('op')}")
    handler(doc, op)
    applied.append(op.get("op"))


# ---- excel ops -------------------------------------------------------
def _rows_for(doc: Any, op: dict[str, Any]) -> list[int]:
    from artpm_agent.editing.edit_interpreter import _find_rows  # local import
    df = doc.active_df
    # set_cell identifies the row by a *label* column (e.g. 角色), so it must
    # search every column; other ops are scoped to their own column.
    column = op.get("column") if op.get("op") != "set_cell" else None
    return _find_rows(df, column, op.get("criteria", {}))


def _set_cell(doc: Any, op: dict[str, Any]) -> None:
    df = doc.sheets[op["sheet"]]
    col = op["column"]
    if col not in df.columns:
        raise ValueError(f"column not found: {col}")
    rows = _rows_for(doc, op)
    if not rows:
        raise ValueError("no matching row")
    for i in rows:
        df.iloc[i, df.columns.get_loc(col)] = op["value"]


def _replace_in_column(doc: Any, op: dict[str, Any]) -> None:
    df = doc.sheets[op["sheet"]]
    col = op["column"]
    if col not in df.columns:
        raise ValueError(f"column not found: {col}")
    loc = df.columns.get_loc(col)
    for i in range(len(df)):
        val = df.iloc[i, loc]
        if val is not None and op["find"] in str(val):
            df.iloc[i, loc] = str(val).replace(op["find"], op["replace"])


def _delete_row(doc: Any, op: dict[str, Any]) -> None:
    df = doc.sheets[op["sheet"]]
    rows = sorted(_rows_for(doc, {**op, "column": None}), reverse=True)
    if not rows:
        raise ValueError("no matching row to delete")
    doc.sheets[op["sheet"]] = df.drop(index=rows).reset_index(drop=True)


def _add_row(doc: Any, op: dict[str, Any]) -> None:
    df = doc.sheets[op["sheet"]]
    raw = op.get("values", {}) or {}
    if isinstance(raw, list):  # positional: map onto columns in order
        new_row = {c: (raw[i] if i < len(raw) else None) for i, c in enumerate(df.columns)}
    else:  # dict: column -> value
        new_row = {c: raw.get(c) for c in df.columns}
    after = op.get("after")
    frame = df if after is None else df.iloc[:after]
    rest = df.iloc[after:] if after is not None else df.iloc[0:0]
    doc.sheets[op["sheet"]] = pd.concat([frame, pd.DataFrame([new_row]), rest], ignore_index=True)


def _sort(doc: Any, op: dict[str, Any]) -> None:
    df = doc.sheets[op["sheet"]]
    col = op["column"]
    if col not in df.columns:
        raise ValueError(f"column not found: {col}")
    doc.sheets[op["sheet"]] = df.sort_values(
        by=col, ascending=op.get("ascending", True), kind="stable"
    ).reset_index(drop=True)


# ---- word ops --------------------------------------------------------
def _text_replace(doc: Any, op: dict[str, Any]) -> None:
    for block in doc.blocks:
        text = block.get("text")
        if isinstance(text, str) and op["find"] in text:
            block["text"] = text.replace(op["find"], op["replace"])


def _delete_block(doc: Any, op: dict[str, Any]) -> None:
    criteria = op.get("criteria", {})
    target = criteria.get("contains", "")
    kept = [
        b for b in doc.blocks
        if not (isinstance(b.get("text"), str) and target in b.get("text", ""))
    ]
    if len(kept) == len(doc.blocks):
        raise ValueError("no matching block to delete")
    doc.blocks = kept


def _add_block(doc: Any, op: dict[str, Any]) -> None:
    doc.blocks.append(dict(op.get("block", {"type": "paragraph", "text": ""})))
