"""
Quality Control Skill - 质量把控

美术外包场景下的质量评审工作流：
  1. 提交评审 (submit)  —— 任务进入「待审核」
  2. 记录评审 (review)  —— 通过 / 驳回 / 返工 状态机 + 质量评分(1-5)
  3. 质检报告 (report)  —— 聚合项目质量指标（通过率 / 平均分 / 待返工）

复用 Task 已有字段 quality_score(1-5) 与 revision_count，
写入通过 DatabaseManager（若存在 update_task）持久化，否则仅返回结果不落库。

QA 阈值优先读取 config.qa，缺失时回退到 DEFAULT_QA_CONFIG（不强制修改配置文件）。
"""
from typing import Dict, Any, Optional

from .base_skill import BaseSkill


DEFAULT_QA_CONFIG = {
    "pass_score": 3.0,          # 质量分 >= 此值视为通过
    "excellent_score": 4.5,     # 优秀线
    "reject_threshold": 2.0,    # 低于此分视为不达标（报告中标红）
    "max_revisions_warn": 3,    # 返工次数达到此值告警
}

_DECISION_ALIASES = {
    "accept": "accept", "通过": "accept", "通过评审": "accept",
    "reject": "reject", "驳回": "reject", "不通过": "reject",
    "revise": "revise", "返工": "revise", "修改": "revise", "打回": "revise",
}
_DECISION_MESSAGE = {
    "accept": "✅ 评审通过，任务已标记完成。",
    "reject": "❌ 评审驳回，已退回「进行中」，需重做。",
    "revise": "🔁 需修改，已退回「待审核」。",
}


class QualityControlSkill(BaseSkill):
    """美术外包质量把控：提交评审、通过/驳回/返工状态机、质量评分与质检报告"""

    skill_name = "quality_control"
    description = "美术外包质量把控：提交评审、通过/驳回/返工状态机、质量评分与质检报告"
    version = "1.0"
    requires_llm = False

    def __init__(self, context: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        cfg = self.config or {}
        qa_cfg = cfg.get("qa") if isinstance(cfg, dict) else None
        self.qa = {**DEFAULT_QA_CONFIG, **(qa_cfg or {})}
        self.db = self.context.get("database") if self.context else None

    # ── persistence helpers ──
    def _load_task(self, task_id: Optional[int], task_name: Optional[str]):
        if self.db is None or not hasattr(self.db, "get_task"):
            return None
        if task_id is not None:
            try:
                return self.db.get_task(int(task_id))
            except Exception:
                return None
        if task_name:
            tasks = self.db.get_tasks() if hasattr(self.db, "get_tasks") else []
            for t in tasks:
                if getattr(t, "task_name", None) == task_name:
                    return t
        return None

    def _persist(self, task, fields: Dict[str, Any]) -> bool:
        if self.db is None or not hasattr(self.db, "update_task"):
            return False
        try:
            self.db.update_task(task.id, **fields)
            return True
        except Exception as e:  # pragma: no cover - defensive
            self._log(f"质量结果持久化失败: {e}", "WARNING")
            return False

    # ── actions ──
    def submit_for_review(self, task_id=None, task_name=None, submitter=None) -> Dict[str, Any]:
        task = self._load_task(task_id, task_name)
        if task is None:
            return {"success": False, "error": "未找到对应任务", "task_id": task_id}
        if getattr(task, "status", None) == "已完成":
            return {"success": False, "error": "任务已完成，无需再次提交评审", "task_id": task.id}
        prev = task.status
        self._persist(task, {"status": "待审核"})
        return {
            "success": True,
            "task_id": task.id,
            "task_name": task.task_name,
            "previous_status": prev,
            "status": "待审核",
            "message": f"任务「{task.task_name}」已提交评审。",
        }

    def record_review(self, task_id=None, task_name=None, decision=None,
                      quality_score=None, reviewer=None, notes=None) -> Dict[str, Any]:
        norm = _DECISION_ALIASES.get((decision or "").strip())
        if norm is None:
            return {
                "success": False,
                "error": "decision 必须是 accept/reject/revise（通过/驳回/返工）",
            }
        task = self._load_task(task_id, task_name)
        if task is None:
            return {"success": False, "error": "未找到对应任务", "task_id": task_id}

        score = task.quality_score
        if quality_score is not None:
            try:
                score = float(quality_score)
                if not 0 <= score <= 5:
                    return {"success": False, "error": "质量分必须在 0-5 之间"}
            except (TypeError, ValueError):
                return {"success": False, "error": "质量分必须是数字"}
        if norm == "accept" and score is None:
            return {"success": False, "error": "通过评审必须给出质量分(0-5)"}

        revision_count = int(getattr(task, "revision_count", 0) or 0)
        fields: Dict[str, Any] = {}
        if norm == "accept":
            fields = {"status": "已完成", "quality_score": score}
        elif norm == "reject":
            revision_count += 1
            fields = {"status": "进行中", "revision_count": revision_count, "quality_score": score}
        else:  # revise
            revision_count += 1
            fields = {"status": "待审核", "revision_count": revision_count, "quality_score": score}

        self._persist(task, fields)
        return {
            "success": True,
            "task_id": task.id,
            "task_name": task.task_name,
            "decision": norm,
            "quality_score": score,
            "revision_count": revision_count,
            "status": fields.get("status"),
            "reviewer": reviewer,
            "notes": notes,
            "message": _DECISION_MESSAGE[norm],
        }

    def quality_report(self, project_id=None) -> Dict[str, Any]:
        tasks = self._project_tasks(project_id)
        if tasks is None:
            return {
                "success": False,
                "error": "无法获取任务数据（数据库不可用或未初始化）",
                "project_id": project_id,
            }
        total = len(tasks)
        reviewed = [t for t in tasks if getattr(t, "quality_score", None) is not None]
        passed = [t for t in reviewed if t.quality_score >= self.qa["pass_score"]]
        scores = [t.quality_score for t in reviewed]
        avg = sum(scores) / len(scores) if scores else 0.0
        needs_rework = [t for t in tasks if int(getattr(t, "revision_count", 0) or 0) >= self.qa["max_revisions_warn"]]
        pending = [t for t in tasks if getattr(t, "status", None) in ("待审核", "进行中")]

        pass_rate = (len(passed) / len(reviewed)) if reviewed else 0.0
        return {
            "success": True,
            "project_id": project_id,
            "total_tasks": total,
            "reviewed_tasks": len(reviewed),
            "passed_tasks": len(passed),
            "pass_rate": round(pass_rate, 4),
            "avg_quality_score": round(avg, 2),
            "pending_review": len(pending),
            "needs_rework": [
                {"task_id": t.id, "task_name": t.task_name,
                 "revision_count": int(getattr(t, "revision_count", 0) or 0)}
                for t in needs_rework
            ],
            "low_quality": [
                {"task_id": t.id, "task_name": t.task_name, "quality_score": t.quality_score}
                for t in reviewed if t.quality_score < self.qa["reject_threshold"]
            ],
            "summary": (
                f"共 {total} 个任务，已评审 {len(reviewed)} 个，通过 {len(passed)} 个"
                f"（通过率 {pass_rate * 100:.0f}%），平均质量分 {avg:.2f}/5，"
                f"{len(pending)} 个待处理。"
            ),
        }

    def _project_tasks(self, project_id):
        if self.db is None or not hasattr(self.db, "get_tasks"):
            return None
        try:
            return self.db.get_tasks(project_id=project_id)
        except Exception:
            return None

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        action = str(inputs.get("action") or "report").strip().lower()
        if action in ("submit", "提交", "提交评审"):
            return self.submit_for_review(
                task_id=inputs.get("task_id"),
                task_name=inputs.get("task_name"),
                submitter=inputs.get("submitter"),
            )
        if action in ("review", "评审", "记录评审", "record"):
            return self.record_review(
                task_id=inputs.get("task_id"),
                task_name=inputs.get("task_name"),
                decision=inputs.get("decision"),
                quality_score=inputs.get("quality_score"),
                reviewer=inputs.get("reviewer"),
                notes=inputs.get("notes"),
            )
        if action in ("report", "报告", "质检报告", "quality_report"):
            return self.quality_report(project_id=inputs.get("project_id"))
        return {
            "success": False,
            "error": f"不支持的 QC 动作: {action}",
            "hint": "支持 action=submit / review / report",
        }
