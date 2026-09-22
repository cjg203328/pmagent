"""Job 一等公民：编排层对象，包装 WorkflowRun，成为 UI/API 的主导航对象。

PRD §4。读写一律通过 `JobService`，不要直接构造 ORM 对象。
"""

from artpm_agent.jobs.service import (
    ALLOWED_TRANSITIONS,
    ARTIFACT_TYPES,
    CANCELLED,
    DRAFT,
    FAILED,
    JOB_STATUSES,
    NEEDS_INPUT,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TERMINAL_STATUSES,
    TRIGGERS,
    VERIFICATION_STATUSES,
    WAITING,
    JobError,
    JobNotFoundError,
    JobService,
    JobStateError,
    can_transition,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ARTIFACT_TYPES",
    "CANCELLED",
    "DRAFT",
    "FAILED",
    "JOB_STATUSES",
    "NEEDS_INPUT",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "TERMINAL_STATUSES",
    "TRIGGERS",
    "VERIFICATION_STATUSES",
    "WAITING",
    "JobError",
    "JobNotFoundError",
    "JobService",
    "JobStateError",
    "can_transition",
]
