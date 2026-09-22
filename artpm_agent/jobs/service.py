"""Job 服务：建、列、查、状态推进、成果挂载。

PRD §4 的硬判据：**Job 必须能在无人对话时被创建、推进、完成**。因此本模块
没有任何函数要求 `conversation_id`；对话只是 Job 的一个可选通道。

这一层是**编排层**，不是第二条执行主链——`run_turn()` 仍是唯一执行入口
（PRD §4）。本模块只负责 Job 自身的状态与成果归属，不调用技能。

状态机（`prototype/交互规格.md` §1）：

    draft → queued → running → succeeded
                        │  ├→ waiting → running
                        │  ├→ needs_input → running
                        │  └→ failed
    任意非终态 → cancelled

非法的状态跳转一律抛 `JobStateError`，不静默接受——静默接受会让「进度条卡在
running」这类问题永远查不出来。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, cast

from sqlalchemy import func

from artpm_agent.database.models import DatabaseManager, Job, JobArtifact

DRAFT = "draft"
QUEUED = "queued"
RUNNING = "running"
WAITING = "waiting"
NEEDS_INPUT = "needs_input"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

JOB_STATUSES: tuple[str, ...] = (
    DRAFT,
    QUEUED,
    RUNNING,
    WAITING,
    NEEDS_INPUT,
    SUCCEEDED,
    FAILED,
    CANCELLED,
)

TERMINAL_STATUSES: frozenset[str] = frozenset({SUCCEEDED, FAILED, CANCELLED})

TRIGGERS: tuple[str, ...] = ("manual", "scheduled", "from_message")

ARTIFACT_TYPES: tuple[str, ...] = ("xlsx", "docx", "pptx", "pdf")

VERIFICATION_STATUSES: tuple[str, ...] = ("pending", "passed", "failed")

ALLOWED_TRANSITIONS: Dict[str, frozenset[str]] = {
    DRAFT: frozenset({QUEUED, CANCELLED}),
    QUEUED: frozenset({RUNNING, WAITING, NEEDS_INPUT, FAILED, CANCELLED}),
    RUNNING: frozenset({SUCCEEDED, FAILED, WAITING, NEEDS_INPUT, CANCELLED}),
    WAITING: frozenset({RUNNING, FAILED, CANCELLED}),
    NEEDS_INPUT: frozenset({RUNNING, FAILED, CANCELLED}),
    SUCCEEDED: frozenset(),
    FAILED: frozenset({QUEUED, CANCELLED}),
    CANCELLED: frozenset(),
}

# 进入这些状态时记录时间戳
_STARTED_AT_STATUSES = frozenset({RUNNING})
_COMPLETED_AT_STATUSES = TERMINAL_STATUSES


class JobError(RuntimeError):
    """Base class so callers can catch every Job failure with one name."""


class JobStateError(JobError):
    """Raised when a status change is not a legal transition."""


class JobNotFoundError(JobError):
    """Raised when a job id does not exist in the current scope."""


def _now() -> datetime:
    return datetime.now()


def can_transition(current: str, target: str) -> bool:
    """Return whether ``current → target`` is a documented transition."""
    if current not in ALLOWED_TRANSITIONS or target not in JOB_STATUSES:
        return False
    if current == target:
        return True
    return target in ALLOWED_TRANSITIONS[current]


def _dump(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _load(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


class JobService:
    """所有 Job 读写的唯一入口。

    直接构造 ORM 对象并写库会绕过状态机与版本追加规则，因此不提供第二套写法。
    """

    def __init__(self, database: DatabaseManager):
        self.db = database

    # ── 会话 ──
    def _session(self):
        if self.db is None:
            raise JobError("数据库不可用，无法操作 Job")
        return self.db.get_session()

    # ── 建 ──
    def create(
        self,
        job_name: str,
        *,
        project_id: Optional[int] = None,
        job_type: Optional[str] = None,
        trigger: str = "manual",
        inputs: Optional[Mapping[str, Any]] = None,
        conversation_id: Optional[str] = None,
        workflow_run_id: Optional[str] = None,
        schedule_id: Optional[int] = None,
        next_run_at: Optional[datetime] = None,
        status: str = DRAFT,
    ) -> Dict[str, Any]:
        """Create a job. No argument here is required to be a conversation."""
        name = str(job_name or "").strip()
        if not name:
            raise JobError("job_name 不能为空")
        if trigger not in TRIGGERS:
            raise JobError(f"未知触发方式: {trigger}")
        if status not in JOB_STATUSES:
            raise JobError(f"未知 Job 状态: {status}")
        if project_id is not None:
            project_id = _positive_int(project_id, "project_id")

        session = self._session()
        try:
            job = Job(
                job_name=name,
                project_id=project_id,
                job_type=job_type,
                trigger=trigger,
                status=status,
                conversation_id=conversation_id,
                workflow_run_id=workflow_run_id,
                schedule_id=schedule_id,
                next_run_at=next_run_at,
                job_input_json=_dump(dict(inputs) if inputs else None),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            return cast(Dict[str, Any], job.to_dict())
        finally:
            session.close()

    # ── 查 ──
    def get(self, job_id: int) -> Dict[str, Any]:
        session = self._session()
        try:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job is None:
                raise JobNotFoundError(f"Job {job_id} 不存在")
            payload = cast(Dict[str, Any], job.to_dict())
            payload["inputs"] = _load(job.job_input_json)
            payload["outputs"] = _load(job.job_output_json)
            payload["artifacts"] = [
                artifact.to_dict() for artifact in self._artifacts(session, job.id)
            ]
            return payload
        finally:
            session.close()

    def list(
        self,
        *,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        statuses: Optional[Iterable[str]] = None,
        trigger: Optional[str] = None,
        job_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List jobs newest first. Callers group by status for the ① 栏."""
        session = self._session()
        try:
            query = session.query(Job)
            if project_id is not None:
                query = query.filter(
                    Job.project_id == _positive_int(project_id, "project_id")
                )
            if status is not None:
                if status not in JOB_STATUSES:
                    raise JobError(f"未知 Job 状态: {status}")
                query = query.filter(Job.status == status)
            if statuses is not None:
                wanted = list(statuses)
                unknown = [item for item in wanted if item not in JOB_STATUSES]
                if unknown:
                    raise JobError(f"未知 Job 状态: {unknown}")
                query = query.filter(Job.status.in_(wanted))
            if trigger is not None:
                if trigger not in TRIGGERS:
                    raise JobError(f"未知触发方式: {trigger}")
                query = query.filter(Job.trigger == trigger)
            if job_type is not None:
                query = query.filter(Job.job_type == job_type)
            rows = (
                query.order_by(Job.created_at.desc(), Job.id.desc())
                .limit(_positive_int(limit, "limit"))
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def list_grouped(
        self, *, project_id: Optional[int] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Split jobs into the three ① 栏 groups from the interaction spec.

        「计划中」means "will run again on a schedule", i.e. an active job that
        carries `next_run_at`. A one-off draft belongs to 进行中 — it is waiting
        for a human, not for a clock.
        """
        rows = self.list(project_id=project_id, limit=500)
        groups: Dict[str, List[Dict[str, Any]]] = {
            "进行中": [],
            "计划中": [],
            "已完成": [],
        }
        for row in rows:
            if row["status"] in TERMINAL_STATUSES:
                groups["已完成"].append(row)
            elif row.get("next_run_at"):
                groups["计划中"].append(row)
            else:
                groups["进行中"].append(row)
        return groups

    # ── 状态推进 ──
    def transition(
        self,
        job_id: int,
        target: str,
        *,
        error: Optional[str] = None,
        outputs: Optional[Mapping[str, Any]] = None,
        next_run_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Move a job to ``target``, rejecting undocumented transitions."""
        if target not in JOB_STATUSES:
            raise JobError(f"未知 Job 状态: {target}")

        session = self._session()
        try:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job is None:
                raise JobNotFoundError(f"Job {job_id} 不存在")
            current = str(job.status)
            if not can_transition(current, target):
                raise JobStateError(
                    f"不允许的状态跳转: {current} → {target}"
                    f"（{current} 只能转到 "
                    f"{sorted(ALLOWED_TRANSITIONS.get(current, frozenset()))}）"
                )

            job.status = target
            job.updated_at = _now()
            if target in _STARTED_AT_STATUSES and job.started_at is None:
                job.started_at = _now()
            if target in _COMPLETED_AT_STATUSES:
                job.completed_at = _now()
            if target == FAILED:
                job.error = error or job.error or "未提供失败原因"
            elif error is not None:
                job.error = error
            if outputs is not None:
                job.job_output_json = _dump(dict(outputs))
            if next_run_at is not None:
                job.next_run_at = next_run_at

            session.commit()
            session.refresh(job)
            return cast(Dict[str, Any], job.to_dict())
        finally:
            session.close()

    # ── 成果挂载 ──
    def attach_artifact(
        self,
        job_id: int,
        *,
        filename: str,
        artifact_type: Optional[str] = None,
        artifact_path: Optional[str] = None,
        sha256: Optional[str] = None,
        size_bytes: Optional[int] = None,
        step_id: Optional[str] = None,
        verification_status: str = "pending",
        verification: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Attach a produced file, appending a new version for the same name.

        Same `filename` never overwrites: version increments, so a rerun cannot
        destroy the artifact a user already downloaded (PRD §9).
        """
        name = str(filename or "").strip()
        if not name:
            raise JobError("filename 不能为空")
        if verification_status not in VERIFICATION_STATUSES:
            raise JobError(f"未知核验状态: {verification_status}")
        if artifact_type is not None and artifact_type not in ARTIFACT_TYPES:
            raise JobError(f"未知交付物类型: {artifact_type}")

        session = self._session()
        try:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job is None:
                raise JobNotFoundError(f"Job {job_id} 不存在")

            latest = (
                session.query(func.max(JobArtifact.version))
                .filter(JobArtifact.job_id == job_id, JobArtifact.filename == name)
                .scalar()
            )
            version = int(latest or 0) + 1

            artifact = JobArtifact(
                job_id=job_id,
                step_id=step_id,
                filename=name,
                artifact_type=artifact_type or _infer_type(name),
                artifact_path=artifact_path,
                sha256=sha256,
                size_bytes=size_bytes,
                version=version,
                verification_status=verification_status,
                verification_json=_dump(dict(verification) if verification else None),
            )
            session.add(artifact)
            job.updated_at = _now()
            session.commit()
            session.refresh(artifact)
            return artifact.to_dict()
        finally:
            session.close()

    def list_artifacts(self, job_id: int) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job is None:
                raise JobNotFoundError(f"Job {job_id} 不存在")
            return [artifact.to_dict() for artifact in self._artifacts(session, job_id)]
        finally:
            session.close()

    def downloadable(self, job_id: int, *, filename: Optional[str] = None):
        """Return artifacts that passed verification.

        未通过核验的成果不可下载（`prototype/交互规格.md` §3 ③栏）。这里返回
        列表而不是抛错，让 UI 能显示「核验中」而不是「不存在」。
        """
        items = self.list_artifacts(job_id)
        if filename is not None:
            items = [item for item in items if item["filename"] == filename]
        return [item for item in items if item["verification_status"] == "passed"]

    # ── 内部 ──
    @staticmethod
    def _artifacts(session, job_id: int) -> List[JobArtifact]:
        return list(
            session.query(JobArtifact)
            .filter(JobArtifact.job_id == job_id)
            .order_by(JobArtifact.id.asc())
            .all()
        )


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise JobError(f"{field} 必须是正整数")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise JobError(f"{field} 必须是正整数")
    if parsed < 1:
        raise JobError(f"{field} 必须是正整数")
    return parsed


def _infer_type(filename: str) -> Optional[str]:
    lowered = filename.lower()
    for suffix in ARTIFACT_TYPES:
        if lowered.endswith(f".{suffix}"):
            return suffix
    return None


__all__ = [
    "ALLOWED_TRANSITIONS",
    "ARTIFACT_TYPES",
    "CANCELLED",
    "DRAFT",
    "FAILED",
    "JOB_STATUSES",
    "JobError",
    "JobNotFoundError",
    "JobService",
    "JobStateError",
    "NEEDS_INPUT",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "TERMINAL_STATUSES",
    "TRIGGERS",
    "VERIFICATION_STATUSES",
    "WAITING",
    "can_transition",
]
