"""办公流技能：周报。PRD §4.5 办公流技能族的第一个成员。

与业务技能的区别：它不回答「是什么」，而是**把事做完并交付文件**——读取真实的
任务数据，产出可下载的 `.docx` 周报。没有数据时明确报错，不编造内容。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from artpm_agent.jobs import JobService

from .base_skill import BaseSkill
from .input_schemas import BUILTIN_SKILL_INPUT_SCHEMAS

logger = logging.getLogger(__name__)

_DONE = {"已完成", "已验收", "已交付", "completed", "done", "accepted", "delivered"}
_CANCELLED = {"已取消", "cancelled", "canceled"}


def _parse_week_start(value: Any) -> date:
    """Accept an explicit Monday; default to the Monday of the current week."""
    text = str(value or "").strip()
    if text:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"week_start 必须是 YYYY-MM-DD：{text}")
    today = date.today()
    return today - timedelta(days=today.weekday())


def _in_window(value: Any, start: date, days: int) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, datetime):
        value = value.date()
    elif isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip()).date()
        except ValueError:
            return False
    if not isinstance(value, date):
        return False
    return start <= value < start + timedelta(days=days)


def _build_sections(
    tasks: list[Any],
    member_names: dict[int, str],
    week_start: date,
) -> dict[str, list[dict[str, Any]]]:
    progress: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    next_week: list[dict[str, Any]] = []
    today = date.today()

    for task in tasks:
        status = str(getattr(task, "status", "") or "")
        if status in _CANCELLED:
            continue
        done = status in _DONE
        name = str(getattr(task, "task_name", "") or f"任务{getattr(task, 'id', '?')}")
        assignee = getattr(task, "assignee_id", None)
        owner = member_names.get(assignee, "未分配") if isinstance(assignee, int) else "未分配"
        row = {
            "name": name,
            "owner": owner,
            "status": status or "未开始",
            "progress": getattr(task, "progress", 0) or 0,
        }
        if done and _in_window(getattr(task, "completed_date", None), week_start, 7):
            progress.append({**row, "note": "本周完成"})
        elif not done and _in_window(getattr(task, "created_at", None), week_start, 7):
            progress.append({**row, "note": "本周新启动"})
        if not done:
            due = getattr(task, "due_date", None)
            if due is not None and _in_window(due, today - timedelta(days=30), 30):
                due_date = due.date() if isinstance(due, datetime) else due
                if due_date < today:
                    risks.append({**row, "due": due_date.isoformat(), "overdue_days": (today - due_date).days})
                elif due_date <= today + timedelta(days=7) and (getattr(task, "progress", 0) or 0) < 60:
                    risks.append({**row, "due": due_date.isoformat(), "overdue_days": 0})
            if _in_window(due, today + timedelta(days=7), 7):
                next_week.append(row)
    return {"progress": progress, "risks": risks, "next_week": next_week}


def _paragraphs(sections: dict[str, list[dict[str, Any]]], week_start: date) -> list[dict[str, Any]]:
    def heading(text: str) -> dict[str, Any]:
        return {"text": text, "kind": "heading", "level": 2, "bold": False, "italic": False}

    def bullet(text: str) -> dict[str, Any]:
        return {"text": text, "kind": "bullet", "level": 1, "bold": False, "italic": False}

    out: list[dict[str, Any]] = [
        {"text": f"周报（{week_start.isoformat()} 起）", "kind": "heading", "level": 1,
         "bold": False, "italic": False},
        heading("一、本周进展"),
    ]
    if sections["progress"]:
        for row in sections["progress"]:
            out.append(bullet(f"{row['name']}（{row['owner']}）：{row['status']}，进度 {row['progress']}%"))
    else:
        out.append(bullet("本周没有完成或推进中的任务记录。"))
    out.append(heading("二、风险与阻塞"))
    if sections["risks"]:
        for row in sections["risks"]:
            extra = f"，已逾期 {row['overdue_days']} 天" if row["overdue_days"] else "，7 天内到期"
            out.append(bullet(f"{row['name']}（{row['owner']}）：截止 {row['due']}{extra}，进度 {row['progress']}%"))
    else:
        out.append(bullet("未发现逾期或临期低风险任务。"))
    out.append(heading("三、下周计划"))
    if sections["next_week"]:
        for row in sections["next_week"]:
            out.append(bullet(f"{row['name']}（{row['owner']}）：当前 {row['status']}，进度 {row['progress']}%"))
    else:
        out.append(bullet("下周暂无到期任务。"))
    return out


def _resolve_generator(context: Optional[dict[str, Any]], inputs: dict[str, Any]):
    """按 factory 的同一约定定位 artifacts 根目录；解析不到就返回 None。

    返回 None 时技能降级为纯文本周报，并在结果里明确说明没有交付文件——
    静默地「成功但没文件」正是 PRD §10 R-4 要避免的形态。
    """
    if not context:
        return None
    config = context.get("config") or {}
    data_root = str(config.get("data_root") or "data")
    root = (
        Path(data_root).expanduser()
        / "artifacts"
        / str(inputs.get("tenant_id") or "local")
        / str(inputs.get("workspace_id") or "local-default")
        / str(inputs.get("profile_id") or "local-default")
    )
    try:
        from artpm_agent.artifacts.generator import WorkspaceArtifactGenerator

        return WorkspaceArtifactGenerator(root)
    except Exception:
        return None


class WeeklyReportSkill(BaseSkill):
    """读取真实任务数据，产出可下载的 .docx 周报。"""

    skill_name = "weekly_report"
    description = "办公流：按本周真实任务数据生成周报并交付 .docx"
    version = "1.0"
    requires_llm = False
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        database = self.context.get("database") if self.context else None
        if database is None or not hasattr(database, "get_tasks"):
            return {"success": False, "error": "业务数据库不可用，无法生成周报"}

        try:
            week_start = _parse_week_start(inputs.get("week_start"))
        except ValueError as error:
            return {"success": False, "error": str(error)}

        tasks = list(database.get_tasks() or [])
        member_names = {
            member.id: str(getattr(member, "name", "") or f"成员{member.id}")
            for member in (database.list_members() or [])
        }
        sections = _build_sections(tasks, member_names, week_start)
        if not any(sections[key] for key in ("progress", "risks", "next_week")):
            return {
                "success": False,
                "error": (
                    f"{week_start.isoformat()} 起的一周内没有任何任务数据，"
                    "无法生成周报。请先录入任务或指定 week_start。"
                ),
            }

        counts = {key: len(value) for key, value in sections.items()}
        summary = (
            f"周报（{week_start.isoformat()} 起）：进展 {counts['progress']} 项、"
            f"风险 {counts['risks']} 项、下周 {counts['next_week']} 项。"
        )

        # Job 是导航对象（PRD §4）：先登记为 running，交付物挂在它名下，
        # 成功后转 succeeded。生成失败则转 failed，不留"有文件无任务"的半状态。
        jobs = JobService(database)
        job = jobs.create(
            f"周报-{week_start.isoformat()}",
            job_type="office_weekly",
            trigger=str(inputs.get("trigger") or "manual"),
            inputs={"week_start": week_start.isoformat(), "counts": counts},
            status="running",
        )
        artifacts: list[dict[str, Any]] = []
        try:
            generator = _resolve_generator(self.context, inputs)
            if generator is not None:
                artifact = generator.generate_docx(
                    f"周报-{week_start.isoformat()}",
                    _paragraphs(sections, week_start),
                )
                artifacts.append(artifact)
        except Exception as error:
            logger.exception("Weekly report artifact generation failed")
            jobs.transition(job["id"], "failed", error=str(error))
            return {"success": False, "error": f"周报文件生成失败：{error}"}

        for artifact in artifacts:
            verification = artifact.get("verification") or {}
            jobs.attach_artifact(
                job["id"],
                filename=str(artifact.get("name") or ""),
                artifact_type=str(artifact.get("format") or "docx"),
                artifact_path=str(artifact.get("stored_path") or ""),
                sha256=str(artifact.get("sha256") or ""),
                size_bytes=artifact.get("size"),
                verification_status=str(verification.get("status") or "pending"),
                verification=verification,
            )
        jobs.transition(job["id"], "succeeded", outputs={"counts": counts})

        return {
            "success": True,
            "job_id": job["id"],
            "week_start": week_start.isoformat(),
            "counts": counts,
            "sections": sections,
            "artifacts": artifacts,
            "summary": summary,
        }
