"""
Retrospective Skill - 复盘总结

美术外包场景下的复盘总结增强（此前仅原始统计，无专属技能）：
  1. 结项复盘 (report) —— 聚合 Project + Tasks：时间线、成本偏差、质量均分、
     准时率、返工分布，输出结构化复盘报告。
  2. 经验沉淀 (save_lessons) —— 将经验教训写入 KnowledgeBase（自动沉淀，
     供后续 RAG 复用），绑定客户便于检索。

复用 DatabaseManager.get_project / get_tasks / add_knowledge（已新增）。
"""
from datetime import datetime
from typing import Dict, Any, Optional, List

from .base_skill import BaseSkill


def _to_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class RetrospectiveSkill(BaseSkill):
    """复盘总结：结项复盘报告与经验沉淀"""

    skill_name = "retrospective"
    description = "复盘总结：结项复盘报告、经验教训自动沉淀"
    version = "1.0"
    requires_llm = False

    def __init__(self, context: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        self.db = self.context.get("database") if self.context else None

    def _load(self, project_id):
        if self.db is None or not hasattr(self.db, "get_tasks"):
            return None, None
        try:
            project = self.db.get_project(project_id)
            tasks = self.db.get_tasks(project_id=project_id)
            return project, tasks
        except Exception:
            return None, None

    # ── 结项复盘 ──
    def report(self, project_id: int) -> Dict[str, Any]:
        project, tasks = self._load(project_id)
        if project is None:
            return {"success": False, "error": f"项目不存在或未初始化: {project_id}"}
        if tasks is None:
            return {"success": False, "error": "数据库不可用"}

        total = len(tasks)
        completed = [t for t in tasks if getattr(t, "status", None) == "已完成"]
        # 准时率
        on_time = 0
        for t in completed:
            due = _to_date(getattr(t, "due_date", None))
            done = _to_date(getattr(t, "completed_date", None))
            if due is not None and done is not None and done <= due:
                on_time += 1
        on_time_rate = (on_time / len(completed)) if completed else 0.0
        # 质量均分
        scores = [float(getattr(t, "quality_score", 0)) for t in completed
                  if getattr(t, "quality_score", None) is not None]
        avg_quality = (sum(scores) / len(scores)) if scores else 0.0
        # 返工分布
        revisions = [int(getattr(t, "revision_count", 0) or 0) for t in tasks]
        total_revisions = sum(revisions)
        max_revisions = max(revisions) if revisions else 0

        budget = float(getattr(project, "quote_amount", 0) or 0)
        actual = float(getattr(project, "cost", 0) or 0)
        cost_variance = (budget - actual) if (budget or actual) else None

        created = _to_date(getattr(project, "created_at", None))
        completed_date = _to_date(getattr(project, "completed_date", None))
        duration_days = None
        if created and completed_date:
            duration_days = (completed_date - created).days

        return {
            "success": True,
            "project_id": project_id,
            "project_name": getattr(project, "project_name", None),
            "client": getattr(project, "client", None),
            "total_tasks": total,
            "completed_tasks": len(completed),
            "on_time_rate": round(on_time_rate, 4),
            "avg_quality_score": round(avg_quality, 2),
            "total_revisions": total_revisions,
            "max_revisions": max_revisions,
            "budget": round(budget, 2),
            "actual_cost": round(actual, 2),
            "cost_variance": (round(cost_variance, 2) if cost_variance is not None else None),
            "duration_days": duration_days,
            "summary": (
                f"「{getattr(project, 'project_name', '')}」复盘："
                f"{len(completed)}/{total} 任务完成，准时率 {on_time_rate * 100:.0f}%，"
                f"平均质量分 {avg_quality:.2f}/5，返工 {total_revisions} 次"
                f"（成本偏差 {cost_variance if cost_variance is not None else 'N/A'}）。"
            ),
        }

    # ── 经验沉淀 ──
    def save_lessons(self, project_id: int, lessons: List[str],
                     title: Optional[str] = None) -> Dict[str, Any]:
        if self.db is None or not hasattr(self.db, "add_knowledge"):
            return {"success": False, "error": "数据库不可用（缺少 add_knowledge）"}
        project, _ = self._load(project_id)
        client = getattr(project, "client", None) if project else None
        content = "\n".join(f"- {line}" for line in lessons)
        try:
            kb = self.db.add_knowledge(
                title=title or f"项目{project_id}复盘经验",
                content=content,
                category="lessons",
                source="retrospective",
                client=client,
                tags=["复盘", f"project_{project_id}"],
            )
        except Exception as e:
            return {"success": False, "error": f"经验沉淀失败: {e}"}
        return {
            "success": True,
            "knowledge_id": getattr(kb, "id", None),
            "lesson_count": len(lessons),
            "client": client,
            "message": f"已沉淀 {len(lessons)} 条经验教训到知识库。",
        }

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        action = str(inputs.get("action") or "report").strip().lower()
        if action in ("report", "复盘", "复盘报告", "retro"):
            return self.report(project_id=inputs.get("project_id"))
        if action in ("lessons", "经验", "沉淀", "save_lessons"):
            return self.save_lessons(
                project_id=inputs.get("project_id"),
                lessons=inputs.get("lessons") or [],
                title=inputs.get("title"),
            )
        return {"success": False, "error": f"不支持的 action: {action}",
                "hint": "支持 action=report / lessons"}
