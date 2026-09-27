"""Deterministic lookups for questions about uploaded workbooks.

Large workbooks must not be searched by asking the language model to scan a
truncated Markdown excerpt. This module performs a bounded, read-only lookup
against the original workbook and returns both matches and source locations.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
import logging

from .spreadsheet_io import safe_load_workbook

_PERSON_QUERY = re.compile(
    r"(?P<name>[\u4e00-\u9fff]{2,8})"
    r"(?P<suffix>[^。！？\n]{0,20})"
    r"(?:每月|按月|月度|各月)"
    r"(?P<metric>[^。！？\n]{0,20})(?:人天|工时|负载|分配)",
)
_MONTH_SHEET = re.compile(r"(?P<year>\d{2,4})年(?P<month>1[0-2]|0?[1-9])月")
_NAME_LIKE = re.compile(r"^[\u4e00-\u9fffA-Za-z·]{2,16}$")
_MAX_ROWS_PER_SHEET = 5000
_MAX_COLUMNS = 160
_MAX_MATCHES = 100
logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalised_name(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value)).casefold()


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = _text(value).replace(",", "")
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _extract_person(question: str) -> str | None:
    match = _PERSON_QUERY.search(question or "")
    if match:
        return match.group("name")
    # Also support direct questions such as “程榆婷分配了多少人天”.
    for candidate in re.findall(r"[\u4e00-\u9fff]{2,8}", question or ""):
        if candidate not in {"每月", "按月", "月度", "多少人天", "分配了多少"}:
            return candidate
    return None


def _is_person_column(value: Any) -> bool:
    text = _text(value)
    return bool(_NAME_LIKE.fullmatch(text)) and not any(
        token in text for token in ("项目", "任务", "名称", "进度", "状态", "人员")
    )


def _monthly_person_lookup(path: str | Path, person: str) -> dict[str, Any]:
    workbook = safe_load_workbook(path, read_only=True, data_only=True)
    matches: list[dict[str, Any]] = []
    candidate_names: set[str] = set()
    sheets_read = 0
    try:
        for worksheet in workbook.worksheets:
            sheet_month = _MONTH_SHEET.fullmatch(_text(worksheet.title))
            if not sheet_month:
                continue
            sheets_read += 1
            worksheet.reset_dimensions()
            rows = worksheet.iter_rows(
                min_row=1,
                max_row=_MAX_ROWS_PER_SHEET,
                max_col=_MAX_COLUMNS,
                values_only=True,
            )
            buffered = list(rows)
            if not buffered:
                continue
            header_row = max(
                range(min(5, len(buffered))),
                key=lambda index: sum(
                    1 for value in buffered[index] if _is_person_column(value)
                ),
            )
            header = buffered[header_row]
            person_columns = {
                index: _text(value)
                for index, value in enumerate(header)
                if _is_person_column(value)
            }
            candidate_names.update(person_columns.values())
            target_columns = [
                index
                for index, name in person_columns.items()
                if _normalised_name(name) == _normalised_name(person)
            ]
            if not target_columns:
                continue
            total: float = 0
            numeric_cells = 0
            for row in buffered[header_row + 1 :]:
                for column in target_columns:
                    value = _number(row[column] if column < len(row) else None)
                    if value is not None:
                        total += value
                        numeric_cells += 1
            matches.append(
                {
                    "month": f"20{int(sheet_month.group('year')):02d}-{int(sheet_month.group('month')):02d}",
                    "sheet": worksheet.title,
                    "person": person_columns[target_columns[0]],
                    "total": int(total) if total.is_integer() else total,
                    "numeric_cells": numeric_cells,
                }
            )
            if len(matches) >= _MAX_MATCHES:
                break
    finally:
        workbook.close()

    return {
        "query_type": "person_monthly_allocation",
        "person": person,
        "matches": matches,
        "candidate_names": sorted(candidate_names),
        "sheets_read": sheets_read,
        "matched": bool(matches),
        "source_file": Path(path).name,
    }


def query_workbook(question: str, file_paths: list[str]) -> dict[str, Any] | None:
    """Run a deterministic workbook lookup when the question is precise enough."""

    person = _extract_person(question)
    if not person or not file_paths:
        return None
    suffixes = {Path(path).suffix.casefold() for path in file_paths}
    if not suffixes.intersection({".xlsx", ".xlsm", ".xltx", ".xltm"}):
        return None
    for path in file_paths:
        try:
            result = _monthly_person_lookup(path, person)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
            zipfile.BadZipFile,
        ) as error:
            # The parser may have returned a bounded evidence record for a
            # damaged or ephemeral upload. Deterministic lookup is an enhancer;
            # it must never turn the already-safe attachment fallback into a
            # failed turn.
            logger.warning("Workbook lookup skipped for %s: %s", path, error)
            continue
        if result["matched"] or result["candidate_names"]:
            return result
    return None


def format_workbook_query_evidence(result: Mapping[str, Any]) -> str:
    """Format deterministic matches as a compact, model-readable evidence block."""

    lines = [
        "<deterministic_workbook_query>",
        "以下查询结果直接来自原始工作簿单元格，不是模型从 Markdown 摘要推断的。",
        f"查询对象：{result.get('person')}",
        f"文件：{result.get('source_file')}",
        f"读取月份工作表：{result.get('sheets_read')} 张",
    ]
    matches = result.get("matches") or []
    if matches:
        lines.append("精确匹配：")
        for item in matches:
            lines.append(
                f"- {item.get('month')} / Sheet {item.get('sheet')} / "
                f"列 {item.get('person')} / 合计 {item.get('total')}"
            )
    else:
        lines.append("精确匹配：未找到同名列。")
        candidates = result.get("candidate_names") or []
        if candidates:
            lines.append("工作簿中检测到的候选姓名：" + "、".join(candidates))
    lines.append(
        "回答规则：如果用户姓名与候选姓名存在错别字，先明确指出原文姓名，"
        "不得擅自视为同一个人；如果没有精确匹配，说明未找到精确列。"
    )
    lines.append("</deterministic_workbook_query>")
    return "\n".join(lines)


__all__ = ["format_workbook_query_evidence", "query_workbook"]
