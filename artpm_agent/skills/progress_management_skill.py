"""
Progress Management Skill - 进度管理

美术外包场景下的进度管理增强：
  1. 里程碑视图 (milestone_view) —— 按状态聚合任务，给出整体完成度与各阶段分布。
  2. 阻塞卡点 (blockers) —— 识别逾期或长期零进度的任务（不新增数据库字段，
     用 due_date + progress 推导）。
  3. 每日站会摘要 (standup_summary) —— 手动/触发式巡检输出，替代后台调度
     （遵守无后台进程规则；check_interval_hours 仅作提示，不真正起定时器）。

依赖 DatabaseManager.get_tasks / get_project（已有方法）。
"""
from datetime import datetime
from typing import Dict, Any, Optional

from .base_skill import BaseSkill
from .input_schemas import BUILTIN_SKILL_INPUT_SCHEMAS


_STATUS_ORDER = ["待开始", "进行中", "待审核", "已完成", "已取消"]


def _to_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class ProgressManagementSkill(BaseSkill):
    """进度管理：里程碑视图、阻塞卡点、每日站会摘要"""

    skill_name = "progress_management"
    description = "进度管理：里程碑视图、阻塞卡点、每日站会摘要"
    version = "1.0"
    requires_llm = False
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def __init__(self, context: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        cfg = (self.config or {}).get("progress_tracking", {}) if isinstance(self.config, dict) else {}
        self.check_interval_hours = float(cfg.get("check_interval_hours", 24))
        self.db = self.context.get("database") if self.context else None

    def _load(self, project_id):
        if self.db is None or not hasattr(self.db, "get_tasks"):
            return None
        try:
            return self.db.get_tasks(project_id=project_id)
        except Exception:
            return None

    # ── 里程碑视图 ──
    def milestone_view(self, project_id: int) -> Dict[str, Any]:
        tasks = self._load(project_id)
        if tasks is None:
            return {"success": False, "error": "数据库不可用或未初始化"}
        total = len(tasks)
        by_status = {s: 0 for s in _STATUS_ORDER}
        for t in tasks:
            st = getattr(t, "status", None) or "待开始"
            by_status[st] = by_status.get(st, 0) + 1
        done = by_status.get("已完成", 0)
        overall = round(done / total, 4) if total else 0.0
        return {
            "success": True,
            "project_id": project_id,
            "total_tasks": total,
            "by_status": by_status,
            "completed": done,
            "overall_progress": overall,
            "summary": (
                f"共 {total} 个任务，已完成 {done} 个（整体进度 {overall * 100:.0f}%）"
            ),
        }

    # ── 阻塞卡点 ──
    def blockers(self, project_id: int, today: Optional[str] = None) -> Dict[str, Any]:
        tasks = self._load(project_id)
        if tasks is None:
            return {"success": False, "error": "数据库不可用或未初始化"}
        now = _to_date(today) or datetime.now()
        found = []
        for t in tasks:
            st = getattr(t, "status", None)
            progress = int(getattr(t, "progress", 0) or 0)
            due = _to_date(getattr(t, "due_date", None))
            reasons = []
            if st in ("进行中", "待审核") and due is not None and due < now:
                reasons.append(f"已逾期（截止 {due.date().isoformat()}）")
            if st in ("进行中",) and progress == 0:
                reasons.append("长期零进度")
            if reasons:
                found.append({
                    "task_id": getattr(t, "id", None),
                    "task_name": getattr(t, "task_name", None),
                    "status": st,
                    "progress": progress,
                    "due_date": due.date().isoformat() if due else None,
                    "reasons": reasons,
                })
        return {
            "success": True,
            "project_id": project_id,
            "blocker_count": len(found),
            "blockers": found,
            "summary": (
                f"发现 {len(found)} 个阻塞/风险卡点，请优先处理。"
                if found else "当前无阻塞卡点。"
            ),
        }

    # ── 每日站会摘要 ──
    def standup_summary(self, project_id: int, today: Optional[str] = None) -> Dict[str, Any]:
        tasks = self._load(project_id)
        if tasks is None:
            return {"success": False, "error": "数据库不可用或未初始化"}
        view = self.milestone_view(project_id)
        blk = self.blockers(project_id, today=today)
        in_progress = [t for t in tasks if getattr(t, "status", None) == "进行中"]
        return {
            "success": True,
            "project_id": project_id,
            "check_interval_hours": self.check_interval_hours,
            "overall_progress": view.get("overall_progress"),
            "in_progress_count": len(in_progress),
            "blockers": blk.get("blockers", []),
            "blocker_count": blk.get("blocker_count", 0),
            "summary": (
                f"站会摘要：整体进度 {view.get('overall_progress', 0) * 100:.0f}%，"
                f"进行中 {len(in_progress)} 个，阻塞 {blk.get('blocker_count', 0)} 个。"
                f"（建议巡检周期 {self.check_interval_hours:.0f}h，按需手动触发）"
            ),
        }

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        action = str(inputs.get("action") or "view").strip().lower()
        pid = inputs.get("project_id")
        if action in ("view", "里程碑", "milestone", "进度视图"):
            return self.milestone_view(project_id=pid)
        if action in ("blockers", "阻塞", "卡点", "风险"):
            return self.blockers(project_id=pid, today=inputs.get("today"))
        if action in ("standup", "站会", "摘要", "daily"):
            return self.standup_summary(project_id=pid, today=inputs.get("today"))
        return {"success": False, "error": f"不支持的 action: {action}",
                "hint": "支持 action=view / blockers / standup"}
