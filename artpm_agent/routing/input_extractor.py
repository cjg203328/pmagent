"""Deterministic text-to-skill input extraction."""
from collections.abc import Callable, Mapping
import re
from typing import Any

ConfigGetter = Callable[[str, Any], Any]
Context = Mapping[str, Any]
Inputs = dict[str, Any]
Extractor = Callable[[str, str, Context, ConfigGetter], Inputs | None]


def _extract_quote_and_project(
    user_input: str,
    intent: str,
    context: Context,
    config_get: ConfigGetter,
) -> Inputs | None:
    if intent == "quote_calculator":
        def labeled_amount(label: str) -> float | None:
            match = re.search(
                rf"{label}[：:为是]?\s*(\d+(?:\.\d+)?)\s*(万)?",
                user_input,
            )
            if not match:
                return None
            amount = float(match.group(1))
            return amount * 10000 if match.group(2) else amount

        quote_amount = labeled_amount("报价")
        cost = labeled_amount("成本")
        if quote_amount is None or cost is None:
            amounts = [
                float(value) * 10000
                for value in re.findall(r"(\d+(?:\.\d+)?)\s*万", user_input)
            ]
            if quote_amount is None and amounts:
                quote_amount = amounts[0]
            if cost is None and len(amounts) >= 2:
                cost = amounts[1]

        inputs: Inputs = {
            "quote_amount": quote_amount or 0,
            "cost": cost or 0,
            "quote_data": {
                "total_amount": quote_amount or 0,
                "cost": cost or 0,
            },
        }
        profile_inputs = getattr(
            context.get("agent_profile"),
            "quote_skill_inputs",
            None,
        )
        if callable(profile_inputs):
            inputs.update(profile_inputs())
        else:
            inputs["cost_config"] = config_get("cost_config", {})
            inputs["overhead_rate"] = config_get(
                "cost_config.overhead_rate",
                0.15,
            )
        return inputs

    if intent != "project_evaluator":
        return None

    project_data = context.get("project_data")
    if project_data is None:
        quote_match = re.search(
            r"报价[：:为是]?\s*(\d+(?:\.\d+)?)\s*(万)?",
            user_input,
        )
        cost_match = re.search(
            r"成本[：:为是]?\s*(\d+(?:\.\d+)?)\s*(万)?",
            user_input,
        )
        project_data = {
            "quote_amount": (
                float(quote_match.group(1))
                * (10000 if quote_match.group(2) else 1)
                if quote_match
                else 0
            ),
            "cost": (
                float(cost_match.group(1))
                * (10000 if cost_match.group(2) else 1)
                if cost_match
                else 0
            ),
            "deadline": context.get("deadline"),
        }
    return {"project_data": project_data}


def _extract_team_operations(
    user_input: str,
    intent: str,
    context: Context,
    config_get: ConfigGetter,
) -> Inputs | None:
    if intent == "task_allocator":
        tasks = []
        asset = next(
            (item for item in ["角色", "场景"] if item in user_input),
            "",
        )
        production_types = [
            item
            for item in ["建模", "贴图", "动画", "特效", "UI"]
            if item in user_input
        ]
        for production_type in production_types:
            task_name = (
                f"{asset}{production_type}"
                if asset
                else f"{production_type}制作"
            )
            tasks.append(
                {
                    "name": task_name,
                    "type": production_type,
                    "estimated_hours": 8,
                }
            )
        if not tasks and asset:
            tasks.append(
                {
                    "name": f"{asset}制作",
                    "type": asset,
                    "estimated_hours": 8,
                }
            )
        if not tasks:
            tasks = [
                {
                    "name": "示例任务",
                    "type": "通用",
                    "estimated_hours": 8,
                }
            ]
        return {
            "tasks": tasks,
            "project_id": context.get("project_id", ""),
            "constraints": config_get("task_allocation", {}),
        }

    if intent == "progress_tracker":
        return {
            "check_type": "manual",
            "project_id": context.get("project_id"),
            "warning_days_ahead": config_get(
                "progress_tracking.warning_days_ahead",
                1,
            ),
        }

    if intent != "reminder_bot":
        return None

    if any(word in user_input for word in ["紧急", "急", "urgent"]):
        tone = "urgent"
    elif any(word in user_input for word in ["正式", "formal"]):
        tone = "formal"
    else:
        tone = "friendly"
    return {
        "type": "进度催办",
        "recipients": ["团队成员"],
        "task_id": context.get("task_id", ""),
        "tone": tone,
    }


def _extract_file_and_data(
    user_input: str,
    intent: str,
    context: Context,
    _config_get: ConfigGetter,
) -> Inputs | None:
    if intent == "document_classifier_parser":
        return {
            "file_path": context.get("file_path", ""),
            "source": "local",
        }
    if intent == "file_search":
        extension_aliases = {
            "excel": "xlsx",
            "markdown": "md",
            "文本": "txt",
        }
        extension = None
        for alias, normalized in extension_aliases.items():
            if alias.lower() in user_input.lower():
                extension = normalized
                break
        if extension is None:
            match = re.search(
                r"\b(xlsx|xls|csv|json|pdf|md|txt|py)\b",
                user_input,
                re.I,
            )
            extension = match.group(1).lower() if match else None
        return {
            "pattern": f"*.{extension}" if extension else "*",
            "directory": context.get("directory"),
            "limit": context.get("limit", 100),
        }
    if intent == "file_reader":
        return {
            "file_path": context.get("file_path", ""),
            "lines_limit": context.get("lines_limit", 200),
        }
    if intent == "data_analyzer":
        return {
            "data_source": context.get("data_source"),
            "analysis_type": context.get("analysis_type", "descriptive"),
        }
    if intent == "trend_analyzer":
        return {
            "data_series": context.get("data_series"),
            "time_column": context.get("time_column"),
            "value_column": context.get("value_column"),
            "forecast": context.get("forecast", False),
        }
    return None


def _extract_quality_and_requirements(
    user_input: str,
    intent: str,
    context: Context,
    _config_get: ConfigGetter,
) -> Inputs | None:
    if intent == "quality_control":
        if "验收单" in user_input:
            action, decision = "report", None
        elif any(word in user_input for word in ["提交评审", "送审", "提交"]):
            action, decision = "submit", None
        elif any(
            word in user_input
            for word in ["驳回", "打回", "不通过", "reject"]
        ):
            action, decision = "review", "reject"
        elif any(word in user_input for word in ["返工", "修改", "revise"]):
            action, decision = "review", "revise"
        elif any(
            word in user_input
            for word in ["验收通过", "通过评审", "验收", "通过", "accept"]
        ):
            action, decision = "review", "accept"
        else:
            action, decision = "report", None
        task_match = re.search(r"(?:任务|task)[_ ]?(\d+)", user_input, re.I)
        score_match = re.search(
            r"(?:质量分?|分数|评分)[：:为是]?\s*(\d(?:\.\d+)?)",
            user_input,
        )
        return {
            "action": action,
            "decision": decision,
            "task_id": (
                int(task_match.group(1))
                if task_match
                else context.get("task_id")
            ),
            "quality_score": (
                float(score_match.group(1)) if score_match else None
            ),
            "project_id": context.get("project_id"),
            "reviewer": context.get("user_name"),
        }

    if intent != "requirements_assessment":
        return None

    if any(
        word in user_input
        for word in ["范围", "确认清单", "需求范围", "清单"]
    ):
        action = "scope"
    elif any(
        word in user_input
        for word in ["落库", "导入", "建库", "建档", "录入"]
    ):
        action = "ingest"
    else:
        action = "assess"
    asset_type = next(
        (
            item
            for item in [
                "角色",
                "场景",
                "特效",
                "动画",
                "ui",
                "道具",
                "怪物",
                "机甲",
                "建筑",
                "地形",
            ]
            if item in user_input
        ),
        None,
    )
    return {
        "action": action,
        "asset_type": asset_type,
        "asset_name": context.get("asset_name"),
        "requirements_text": user_input,
        "asset_types": [asset_type] if asset_type else None,
        "project_name": context.get("project_name"),
        "client": context.get("client"),
        "parsed_data": context.get("parsed_data") or {},
        "document_file_name": context.get("document_file_name"),
    }


def _extract_cost_and_schedule(
    user_input: str,
    intent: str,
    context: Context,
    _config_get: ConfigGetter,
) -> Inputs | None:
    if intent == "cost_control":
        if any(word in user_input for word in ["预算", "已用", "用了多少"]):
            action = "budget"
        elif any(word in user_input for word in ["超支", "告警", "超了"]):
            action = "overrun"
        else:
            action = "estimate"
        hours = None
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(工时|小时|h|人天)",
            user_input,
            re.I,
        )
        if match:
            value = float(match.group(1))
            hours = value * 8 if match.group(2).lower() == "人天" else value
        staff_level = next(
            (
                level
                for level in ["初级", "中级", "中高级", "高级", "资深"]
                if level in user_input
            ),
            "中级",
        )
        quantity_match = re.search(r"(\d+)\s*(?:个|件|份)", user_input)
        project_id = context.get("project_id")
        project_match = re.search(
            r"(?:项目|project)[_ ]?(\d+)",
            user_input,
            re.I,
        )
        if project_match:
            project_id = int(project_match.group(1))
        return {
            "action": action,
            "hours": hours or 0,
            "staff_level": staff_level,
            "quantity": (
                int(quantity_match.group(1)) if quantity_match else 1
            ),
            "project_id": project_id,
            "threshold": 0.9,
        }

    if intent != "quote_scheduling":
        return None

    if "里程碑" in user_input:
        action = "milestone"
    elif any(
        word in user_input
        for word in ["排期", "时间线", "多久", "交付时间", "schedule"]
    ):
        action = "schedule"
    else:
        action = "estimate"
    if any(
        word in user_input
        for word in ["复杂", "complex", "影视级", "高精度"]
    ):
        complexity = "complex"
    elif any(word in user_input for word in ["中等", "medium", "一般"]):
        complexity = "medium"
    elif any(
        word in user_input
        for word in ["简单", "simple", "低模", "基础"]
    ):
        complexity = "simple"
    else:
        complexity = "medium"
    asset_type = next(
        (
            item
            for item in [
                "角色",
                "场景",
                "特效",
                "动画",
                "ui",
                "道具",
                "怪物",
                "机甲",
                "建筑",
                "地形",
            ]
            if item in user_input
        ),
        None,
    )
    quantity_match = re.search(r"(\d+)\s*(?:个|件)", user_input)
    return {
        "action": action,
        "complexity": complexity,
        "asset_type": asset_type,
        "quantity": int(quantity_match.group(1)) if quantity_match else 1,
        "history_factor": 1.0,
        "assets": context.get("assets") or [],
        "start_date": context.get("start_date"),
        "team_size": context.get("team_size", 1),
        "parallel": context.get("parallel", 1),
    }


def _extract_progress_delivery_and_retro(
    user_input: str,
    intent: str,
    context: Context,
    _config_get: ConfigGetter,
) -> Inputs | None:
    project_id = context.get("project_id")
    project_match = re.search(
        r"(?:项目|project)[_ ]?(\d+)",
        user_input,
        re.I,
    )
    if project_match:
        project_id = int(project_match.group(1))

    if intent == "progress_management":
        if any(word in user_input for word in ["阻塞", "卡点", "风险", "逾期"]):
            action = "blockers"
        elif any(word in user_input for word in ["站会", "摘要", "daily", "巡检"]):
            action = "standup"
        else:
            action = "view"
        return {
            "action": action,
            "project_id": project_id,
            "today": context.get("today"),
        }

    if intent == "delivery":
        if any(word in user_input for word in ["验收单", "逐项", "签收"]):
            action = "acceptance"
        elif any(word in user_input for word in ["版本", "ver", "v1", "v2", "v3"]):
            action = "version"
        elif any(word in user_input for word in ["记录交付", "交付记录", "登记交付"]) or (
            "记录" in user_input and "交付" in user_input
        ):
            action = "record"
        else:
            action = "manifest"
        delivery_no = context.get("delivery_no")
        delivery_match = re.search(
            r"(?:交付单号|交付编号|delivery(?:[_ -]?(?:no|number))?)"
            r"\s*[：:为是#]?\s*([A-Za-z0-9][A-Za-z0-9._-]{0,127})",
            user_input,
            re.I,
        )
        if delivery_match:
            delivery_no = delivery_match.group(1)
        return {
            "action": action,
            "project_id": project_id,
            "delivery_no": delivery_no or f"D{project_id or ''}",
            "items": context.get("delivery_items"),
            "delivered_by": context.get("user_name"),
            "title": context.get("delivery_title"),
            "asset_id": context.get("asset_id"),
            "version": context.get("version"),
            "status": context.get("delivery_status", "待审核"),
            "note": context.get("note"),
            "file_ref": context.get("file_ref"),
        }

    if intent == "retrospective":
        action = (
            "lessons"
            if any(
                word in user_input
                for word in ["经验", "沉淀", "教训", "lessons"]
            )
            else "report"
        )
        return {
            "action": action,
            "project_id": project_id,
            "lessons": context.get("lessons") or [],
            "title": context.get("retro_title"),
        }

    return None


_EXTRACTORS: tuple[Extractor, ...] = (
    _extract_quote_and_project,
    _extract_team_operations,
    _extract_file_and_data,
    _extract_quality_and_requirements,
    _extract_cost_and_schedule,
    _extract_progress_delivery_and_retro,
)


def extract_skill_inputs(
    user_input: str,
    intent: str,
    context: Context,
    config_get: ConfigGetter,
) -> Inputs:
    """Build inputs for one routed skill while preserving legacy defaults."""
    for extractor in _EXTRACTORS:
        inputs = extractor(user_input, intent, context, config_get)
        if inputs is not None:
            return inputs
    return {}
