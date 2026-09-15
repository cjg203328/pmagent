"""Pure presentation helpers for skill execution results."""
from collections.abc import Callable, Mapping
import json
from typing import Any

SkillResult = Mapping[str, Any]
ResultFormatter = Callable[[str, SkillResult], str]


def _format_quote_result(_skill_name: str, result: SkillResult) -> str:
    risk_label = {
        "low": "低",
        "medium": "中",
        "high": "高",
    }.get(str(result.get("risk_level", "")).lower(), "待判断")
    currency = str(result.get("currency", "CNY")).upper()
    currency_mark = {
        "CNY": "¥",
        "USD": "$",
        "EUR": "€",
        "JPY": "¥",
    }.get(currency, f"{currency} ")
    return (
        f"📊 **利润分析结果**\n\n"
        f"| 项目 | 金额 |\n"
        f"|------|------|\n"
        f"| 报价金额 | {currency_mark}{result.get('quote_amount', 0):,.2f} |\n"
        f"| 制作成本 | {currency_mark}{result.get('cost', 0):,.2f} |\n"
        f"| 毛利润 | {currency_mark}{result.get('gross_profit', 0):,.2f} |\n"
        f"| 管理费({result.get('management_fee', 0) / max(result.get('quote_amount', 1), 1) * 100:.0f}%) | {currency_mark}{result.get('management_fee', 0):,.2f} |\n"
        f"| 税费 | {currency_mark}{result.get('tax', 0):,.2f} |\n"
        f"| **净利润** | **{currency_mark}{result.get('net_profit', 0):,.2f}** |\n\n"
        f"📈 利润率: **{result.get('profit_rate_percent', 'N/A')}**  "
        f"| 风险等级: **{risk_label}**\n\n"
        + "\n".join(f"• {item}" for item in result.get("recommendations", []))
    )


def _format_team_operations(skill_name: str, result: SkillResult) -> str:
    if skill_name == "task_allocator":
        allocations = result.get("allocations", [])
        lines = [f"📋 **任务分配方案**（共 {result.get('total_tasks', 0)} 个任务）\n"]
        lines.append("| 任务 | 负责人 | 预计工时 | 预计完成 |")
        lines.append("|------|--------|----------|----------|")
        for allocation in allocations:
            lines.append(
                f"| {allocation.get('task_name', '')} | {allocation.get('assigned_to', '')} "
                f"| {allocation.get('estimated_hours', '')}h | {allocation.get('estimated_completion', '')} |"
            )
        return "\n".join(lines)

    if skill_name == "progress_tracker":
        warnings = result.get("warnings", [])
        on_track = result.get("on_track", [])
        lines = [f"📊 **进度检查**（{result.get('check_time', '')}）\n"]
        if warnings:
            lines.append(f"⚠️ **{len(warnings)} 个预警:**")
            for warning in warnings:
                lines.append(f"  • {warning.get('message', '')}")
        if on_track:
            lines.append(f"✅ **{len(on_track)} 个项目正常:**")
            for item in on_track:
                lines.append(
                    f"  • {item.get('project_name', '')} — 剩余 {item.get('days_left', '?')} 天"
                )
        unknown = result.get("unknown", [])
        if unknown:
            lines.append(f"ℹ️ **{len(unknown)} 个项目缺少排期:**")
            for item in unknown:
                lines.append(
                    f"  • {item.get('project_name', '')} — {item.get('message', '')}"
                )
        if not warnings and not on_track and not unknown:
            lines.append("暂无项目数据。")
        return "\n".join(lines)

    if skill_name == "reminder_bot":
        lines = ["**催办消息已准备好**\n"]
        for message in result.get("messages", []):
            lines.append(
                f"  • {message.get('recipient', '')}: {message.get('subject', '')}"
            )
        lines.append("\n当前仅生成消息预览，批准后才会发送。")
        return "\n".join(lines)

    delivered = [
        item
        for item in result.get("delivery_results", [])
        if item.get("success")
    ]
    if result.get("sent_status") == "sent":
        return f"**催办已发送**\n\n已投递 {len(delivered)} 条消息。"
    return (
        "**催办发送需要人工核对**\n\n"
        f"{result.get('error') or result.get('note') or '未取得明确发送结果。'}"
    )


def _format_document_and_analysis(skill_name: str, result: SkillResult) -> str:
    if skill_name == "document_classifier_parser":
        extracted = result.get("extracted_data", {})
        project = extracted.get("project_info", {})
        assets = extracted.get("assets", [])
        lines = [
            "📄 **文档解析结果**\n",
            f"• 文档类型: {result.get('document_type', '未知')}",
            f"• 项目名称: {project.get('project_name', '未知')}",
            f"• 客户: {project.get('client_name', '未知')}",
            f"• 总金额: ¥{extracted.get('total_amount', 0):,.2f}",
            f"• 资产数: {len(assets)}",
        ]
        if assets:
            lines.append("\n**资产列表:**")
            for asset in assets[:10]:
                if isinstance(asset, dict):
                    lines.append(
                        f"  • {asset.get('name', asset)} ×{asset.get('quantity', 1)} "
                        f"@¥{asset.get('unit_price', 0):,.0f}"
                    )
        raw_text = str(result.get("raw_text", "")).strip()
        if raw_text:
            lines.extend(["\n**识别文本:**", raw_text[:6000]])
        return "\n".join(lines)

    if skill_name == "file_search":
        files = result.get("files", [])
        lines = [f"🔍 **找到 {result.get('count', len(files))} 个文件**"]
        lines.extend(f"• `{path}`" for path in files[:20])
        if len(files) > 20:
            lines.append(f"• 另有 {len(files) - 20} 个结果未展开")
        return "\n".join(lines)

    if skill_name == "file_reader":
        metadata = result.get("metadata", {})
        content = result.get("content", "")
        return (
            f"📄 **{metadata.get('file_name', '文件')}**\n\n"
            f"```text\n{content[:4000]}\n```"
        )

    if skill_name == "trend_analyzer":
        return (
            f"📈 **趋势分析：{result.get('trend', '未知')}**\n\n"
            + "\n".join(f"• {item}" for item in result.get("insights", []))
        )

    return (
        f"📋 **项目评估：{result.get('feasibility_score', 0)}/100**\n\n"
        f"• 利润率：{result.get('profit_rate', 0) * 100:.1f}%\n"
        f"• 风险等级：{result.get('risk_level', '未知')}\n"
        + "\n".join(f"• {item}" for item in result.get("recommendations", []))
    )


def _format_requirements_result(_skill_name: str, result: SkillResult) -> str:
    if "checklist" in result:
        lines = ["📝 **需求范围确认清单**\n"]
        for item in result.get("checklist", []):
            lines.append(f"• [ ] {item.get('item')} —— {item.get('detail')}")
        lines.append(f"\n共 {result.get('count')} 项，请逐项确认后再发起报价与排期。")
        return "\n".join(lines)
    if "asset_count" in result or "document_id" in result:
        return (
            f"🗂️ **需求已落库**\n\n"
            f"• 项目ID: {result.get('project_id')}\n"
            f"• 资产数: {result.get('asset_count')}\n"
            f"• {result.get('message', '')}"
        )
    return (
        f"🧩 **需求复杂度评估**\n\n"
        f"• 资产类型: {result.get('asset_type') or '未指定'}\n"
        f"• 判定: **{result.get('complexity')}**（评分 {result.get('score')}）\n"
        f"• 依据: {result.get('message', '')}"
    )


def _format_cost_result(_skill_name: str, result: SkillResult) -> str:
    utilization_rate = result.get("utilization_rate")
    utilization_text = (
        "不可用"
        if utilization_rate is None
        else f"{float(utilization_rate) * 100:.0f}%"
    )
    if "alert" in result:
        level = {
            "critical": "🔴 严重",
            "warning": "🟡 预警",
            "ok": "🟢 正常",
        }.get(str(result.get("level") or ""), "")
        return (
            f"💸 **成本超支告警** {level}\n\n"
            f"• 预算: {result.get('budget'):,.0f}\n"
            f"• 已用: {result.get('spent'):,.0f}\n"
            f"• 利用率: {utilization_text}"
            f"（阈值 {result.get('threshold', 0.9) * 100:.0f}%）\n"
            f"• {result.get('message', '')}"
        )
    if "utilization_rate" in result:
        return (
            f"💰 **预算跟踪**\n\n"
            f"• 项目: {result.get('project_name') or result.get('project_id')}\n"
            f"• 预算: {result.get('budget'):,.0f}\n"
            f"• 已用: {result.get('spent'):,.0f}\n"
            f"• 剩余: {result.get('remaining'):,.0f}"
            f"（利用率 {utilization_text}）"
        )
    if "total_cost" in result:
        return (
            f"🧮 **成本估算**\n\n"
            f"• 级别: {result.get('staff_level')}（{result.get('daily_cost')}/天）\n"
            f"• 工时: {result.get('hours')}h × {result.get('quantity')}个\n"
            f"• 人工: {result.get('labor_cost'):,.0f}\n"
            f"• 管理费({float(result.get('overhead_rate') or 0) * 100:.0f}%): {result.get('overhead_cost'):,.0f}\n"
            f"• 税({float(result.get('tax_rate') or 0) * 100:.0f}%): {result.get('tax_cost'):,.0f}\n"
            f"• **合计: {result.get('total_cost'):,.0f}**"
        )
    return f"💡 {result.get('message') or result.get('summary', '')}"


def _format_schedule_and_progress(skill_name: str, result: SkillResult) -> str:
    if skill_name == "quote_scheduling":
        if "timeline" in result:
            lines = [
                f"📅 **排期时间线**（预计 {result.get('finish_date')} 完成，"
                f"约 {result.get('total_man_days')} 人天）\n"
            ]
            lines.append("| 资产 | 复杂度 | 人天 | 起 | 止 |")
            lines.append("|------|--------|------|----|----|")
            for item in result.get("timeline", []):
                lines.append(
                    f"| {item.get('asset_name')} | {item.get('complexity')} | "
                    f"{item.get('man_days')} | {item.get('start')} | {item.get('end')} |"
                )
            return "\n".join(lines)
        if "milestones" in result:
            lines = [f"🏁 **里程碑计划**（预计 {result.get('finish_date')} 验收）\n"]
            for milestone in result.get("milestones", []):
                lines.append(
                    f"• {milestone.get('phase')} —— {milestone.get('date')}：{milestone.get('note')}"
                )
            return "\n".join(lines)
        if "man_days" in result:
            return (
                f"⏱️ **人天估算**\n\n"
                f"• 复杂度: {result.get('complexity')}\n"
                f"• 基准工时: {result.get('base_hours')}h × {result.get('quantity')}个"
                f" × 系数 {result.get('history_factor')}\n"
                f"• **约 {result.get('total_hours')} 工时 ≈ {result.get('man_days')} 人天**"
            )
        return f"📐 {result.get('message') or result.get('summary', '')}"

    if "in_progress_count" in result:
        blockers = result.get("blockers", [])
        blocker_lines = "\n".join(
            f"  • {item.get('task_name')}：{'；'.join(item.get('reasons', []))}"
            for item in blockers
        ) or "  无"
        return (
            f"🗣️ **每日站会摘要**\n\n"
            f"• 整体进度: {result.get('overall_progress', 0) * 100:.0f}%\n"
            f"• 进行中: {result.get('in_progress_count')} 个\n"
            f"• 阻塞: {result.get('blocker_count')} 个\n"
            f"  阻塞明细:\n{blocker_lines}\n"
            f"• 建议巡检周期: {result.get('check_interval_hours'):.0f}h（手动触发）"
        )
    if "by_status" in result:
        lines = [
            f"📊 **里程碑进度视图**（整体 {result.get('overall_progress', 0) * 100:.0f}%）\n"
        ]
        for status, count in result.get("by_status", {}).items():
            lines.append(f"• {status}: {count}")
        return "\n".join(lines)
    if "blockers" in result:
        blockers = result.get("blockers", [])
        if not blockers:
            return "✅ 当前无阻塞/风险卡点。"
        lines = [f"⚠️ **阻塞/风险卡点**（{result.get('blocker_count')} 个）\n"]
        for item in blockers:
            lines.append(
                f"• {item.get('task_name')}（{item.get('status')}）："
                f"{'；'.join(item.get('reasons', []))}"
            )
        return "\n".join(lines)
    return f"📈 {result.get('summary', '')}"


def _format_delivery_result(_skill_name: str, result: SkillResult) -> str:
    if "rows" in result:
        lines = [f"✅ **验收单**（{result.get('summary', '')}）\n"]
        lines.append("| 资产 | 验收标准 | 状态 | 签收 |")
        lines.append("|------|----------|------|------|")
        for row in result.get("rows", []):
            lines.append(
                f"| {row.get('asset_name')} | {row.get('acceptance_criteria')} | "
                f"{row.get('status')} | {row.get('sign_off')} |"
            )
        return "\n".join(lines)
    if "delivery_id" in result:
        return (
            f"📦 **交付已记录**\n\n"
            f"• 交付单号: {result.get('delivery_no')}\n"
            f"• 资产数: {result.get('item_count')}\n"
            f"• {result.get('message', '')}"
        )
    if "version_id" in result:
        return (
            f"🔖 **版本已记录**\n\n"
            f"• 资产ID: {result.get('asset_id')}\n"
            f"• 版本: {result.get('version')}（{result.get('status')}）\n"
            f"• {result.get('message', '')}"
        )
    if "items" in result:
        lines = [f"📋 **交付清单**（{result.get('summary', '')}）\n"]
        lines.append("| 资产 | 类型 | 状态 | 进度 | 最新版本 |")
        lines.append("|------|------|------|------|----------|")
        for item in result.get("items", []):
            lines.append(
                f"| {item.get('asset_name')} | {item.get('asset_type')} | "
                f"{item.get('status')} | {item.get('progress')} | "
                f"{item.get('latest_version') or '-'} |"
            )
        return "\n".join(lines)
    return f"📮 {result.get('message') or result.get('summary', '')}"


def _format_retrospective_result(_skill_name: str, result: SkillResult) -> str:
    if "knowledge_id" in result:
        return (
            f"📚 **经验已沉淀**\n\n"
            f"• 条数: {result.get('lesson_count')}\n"
            f"• 客户: {result.get('client')}\n"
            f"• {result.get('message', '')}"
        )
    cost_variance = result.get("cost_variance")
    return (
        f"📊 **结项复盘报告**\n\n"
        f"• 项目: {result.get('project_name')}（客户: {result.get('client')}）\n"
        f"• 任务: {result.get('completed_tasks')}/{result.get('total_tasks')} 完成\n"
        f"• 准时率: {result.get('on_time_rate', 0) * 100:.0f}%\n"
        f"• 平均质量分: {result.get('avg_quality_score')}/5\n"
        f"• 返工: {result.get('total_revisions')} 次（最多 {result.get('max_revisions')} 次/任务）\n"
        f"• 预算: {result.get('budget'):,.0f} / 实际: {result.get('actual_cost'):,.0f}"
        f"（偏差 {cost_variance if cost_variance is not None else 'N/A'}）\n"
        f"• 周期: {result.get('duration_days')} 天"
    )


_FORMATTERS: dict[str, ResultFormatter] = {
    "quote_calculator": _format_quote_result,
    "task_allocator": _format_team_operations,
    "progress_tracker": _format_team_operations,
    "reminder_bot": _format_team_operations,
    "reminder_dispatch": _format_team_operations,
    "document_classifier_parser": _format_document_and_analysis,
    "file_search": _format_document_and_analysis,
    "file_reader": _format_document_and_analysis,
    "trend_analyzer": _format_document_and_analysis,
    "project_evaluator": _format_document_and_analysis,
    "requirements_assessment": _format_requirements_result,
    "cost_control": _format_cost_result,
    "quote_scheduling": _format_schedule_and_progress,
    "progress_management": _format_schedule_and_progress,
    "delivery": _format_delivery_result,
    "retrospective": _format_retrospective_result,
}


def format_skill_result(skill_name: str, result: SkillResult) -> str:
    """Render a skill result without depending on agent runtime state."""
    if not result.get("success"):
        return (
            f"⚠️ 执行 {skill_name} 时出现问题："
            f"{result.get('error', '未知错误')}"
        )

    formatter = _FORMATTERS.get(skill_name)
    if formatter is not None:
        return formatter(skill_name, result)

    return (
        f"✅ {skill_name} 执行成功。\n```json\n"
        f"{json.dumps(result, ensure_ascii=False, indent=2)}\n```"
    )
