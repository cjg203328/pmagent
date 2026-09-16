"""Pure projection helpers for approval panels.

The actual buttons and persistence remain in ``ui_helpers``.  Keeping payload
projection here prevents UI labels from becoming part of the permission store
contract and makes redacted summaries straightforward to test.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


PERMISSION_IMPACT_LABELS = {
    "quality_control": "将更新质量评审、验收或返工状态",
    "requirements_assessment": "将把需求评估结果写入当前工作区",
    "cost_control": "将更新成本、预算或超支记录",
    "quote_scheduling": "将写入排期、工期或里程碑数据",
    "progress_management": "将更新项目进度、卡点或站会记录",
    "delivery": "将写入交付、验收或资产版本记录",
    "retrospective": "将沉淀复盘结果或经验规则",
    "reminder_dispatch": "将向工作区外部发送催办消息",
}

PERMISSION_PARAMETER_LABELS = {
    "action": "操作",
    "project_id": "项目",
    "asset_id": "资产",
    "task_id": "任务",
    "delivery_no": "交付单号",
    "version": "版本",
    "recipient": "接收方",
    "path": "路径",
    "command": "命令",
}


def thaw_permission_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): thaw_permission_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_permission_json(item) for item in value]
    return value


def permission_parameter_summary(payload_preview: Any) -> str:
    """Build a short summary from an already-redacted public payload."""
    if not isinstance(payload_preview, Mapping):
        return "无附加参数"
    values = payload_preview.get("inputs")
    if not isinstance(values, Mapping):
        values = payload_preview.get("arguments")
    if not isinstance(values, Mapping):
        return "无附加参数"

    parts: list[str] = []
    for key, value in values.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        label = PERMISSION_PARAMETER_LABELS.get(str(key), str(key))
        if isinstance(value, bool):
            rendered = "是" if value else "否"
        elif isinstance(value, (dict, list, tuple)):
            rendered = json.dumps(
                thaw_permission_json(value), ensure_ascii=False, separators=(",", ":")
            )
        else:
            rendered = str(value)
        rendered = rendered.replace("\n", " ").strip()
        if len(rendered) > 56:
            rendered = f"{rendered[:53]}..."
        parts.append(f"{label}={rendered}")
        if len(parts) >= 4:
            break
    return " · ".join(parts) if parts else "无附加参数"


def permission_impact(request: Any, skill_name: str | None) -> str:
    impact = PERMISSION_IMPACT_LABELS.get(skill_name or "")
    if impact:
        return impact
    risk = getattr(request, "risk", None)
    if risk in {"critical", "untrusted"}:
        return "可能访问外部系统或敏感资源"
    if risk == "high":
        return "可能产生外部影响或不可逆修改"
    return "可能修改当前工作区中的数据"


__all__ = [
    "PERMISSION_IMPACT_LABELS",
    "PERMISSION_PARAMETER_LABELS",
    "permission_impact",
    "permission_parameter_summary",
    "thaw_permission_json",
]
