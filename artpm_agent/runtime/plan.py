"""Plan collaboration: draft -> propose -> approve/reject -> execute.

Mirrors the deepseek-harness plan capability: a task is broken into explicit
steps, the plan is submitted for host review, the host approves it or sends
feedback that sends it back to revision, and only an approved plan may be
executed step by step.

High-risk steps carry ``requires_approval``; execution of such a step is
blocked unless the host confirmed it explicitly via ``confirm_steps`` — the
plan-level approval alone is not enough for them (the project's existing
"write tools require host approval" rule, applied to plans).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence
from uuid import uuid4

PLAN_FILENAME = "plans.json"
MAX_TITLE_CHARS = 200
MAX_STEPS = 64


class PlanStatus(str, Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTED = "executed"


class PlanStepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class PlanError(ValueError):
    """Raised when a plan state transition is invalid."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One executable step of a plan."""

    title: str
    description: str = ""
    status: PlanStepStatus = PlanStepStatus.PENDING
    requires_approval: bool = False
    result: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise PlanError("step title must be a non-empty string")
        if not isinstance(self.description, str):
            raise TypeError("step description must be a string")
        if not isinstance(self.status, PlanStepStatus):
            object.__setattr__(self, "status", PlanStepStatus(self.status))
        if not isinstance(self.requires_approval, bool):
            raise TypeError("step requires_approval must be a boolean")
        if self.result is not None and not isinstance(self.result, str):
            raise TypeError("step result must be a string or None")
        object.__setattr__(self, "title", self.title.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "requires_approval": self.requires_approval,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PlanStep":
        return cls(
            title=value["title"],
            description=value.get("description", ""),
            status=value.get("status", PlanStepStatus.PENDING.value),
            requires_approval=bool(value.get("requires_approval", False)),
            result=value.get("result"),
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """An immutable snapshot of a plan at one point in time."""

    plan_id: str
    title: str
    objective: str
    status: PlanStatus
    steps: tuple[PlanStep, ...]
    feedback: str = ""
    confirmed_steps: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise PlanError("plan_id must be a non-empty string")
        if not isinstance(self.status, PlanStatus):
            object.__setattr__(self, "status", PlanStatus(self.status))
        if not isinstance(self.steps, tuple):
            object.__setattr__(self, "steps", tuple(self.steps))
        if not isinstance(self.confirmed_steps, tuple):
            object.__setattr__(
                self, "confirmed_steps", tuple(self.confirmed_steps)
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "objective": self.objective,
            "status": self.status.value,
            "steps": [step.to_dict() for step in self.steps],
            "feedback": self.feedback,
            "confirmed_steps": list(self.confirmed_steps),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Plan":
        return cls(
            plan_id=value["plan_id"],
            title=value["title"],
            objective=value.get("objective", ""),
            status=value.get("status", PlanStatus.DRAFT.value),
            steps=tuple(PlanStep.from_dict(item) for item in value.get("steps", [])),
            feedback=value.get("feedback", ""),
            confirmed_steps=tuple(value.get("confirmed_steps", [])),
            created_at=value.get("created_at", ""),
            updated_at=value.get("updated_at", ""),
        )


class PlanStore:
    """Thread-safe JSON-file persistence for plans (atomic writes)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load_all(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save_all(self, records: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def save(self, plan: Plan) -> None:
        with self._lock:
            records = self._load_all()
            records[plan.plan_id] = plan.to_dict()
            self._save_all(records)

    def get(self, plan_id: str) -> Optional[Plan]:
        with self._lock:
            record = self._load_all().get(plan_id)
        return Plan.from_dict(record) if record else None

    def list(self) -> tuple[Plan, ...]:
        with self._lock:
            records = self._load_all()
        plans = [Plan.from_dict(record) for record in records.values()]
        return tuple(sorted(plans, key=lambda plan: plan.updated_at, reverse=True))

    def delete(self, plan_id: str) -> bool:
        with self._lock:
            records = self._load_all()
            if plan_id not in records:
                return False
            records.pop(plan_id)
            self._save_all(records)
            return True


#: Step runner contract: ``(plan, step) -> optional result string``.
StepRunner = Callable[[Plan, PlanStep], Optional[str]]


class PlanCoordinator:
    """State machine for draft -> propose -> approve/reject -> execute."""

    def __init__(self, store: PlanStore):
        if not isinstance(store, PlanStore):
            raise TypeError("store must be a PlanStore")
        self.store = store

    # ── lifecycle ──

    def create_plan(
        self,
        title: str,
        objective: str,
        steps: Sequence[str | dict[str, Any]],
    ) -> Plan:
        if not isinstance(title, str) or not title.strip():
            raise PlanError("plan title must be a non-empty string")
        if len(title) > MAX_TITLE_CHARS:
            raise PlanError(f"plan title exceeds {MAX_TITLE_CHARS} characters")
        if not isinstance(objective, str) or not objective.strip():
            raise PlanError("plan objective must be a non-empty string")
        if not steps:
            raise PlanError("a plan must contain at least one step")
        if len(steps) > MAX_STEPS:
            raise PlanError(f"a plan cannot exceed {MAX_STEPS} steps")
        parsed: list[PlanStep] = []
        for item in steps:
            if isinstance(item, str):
                parsed.append(PlanStep(title=item))
            elif isinstance(item, dict):
                parsed.append(PlanStep.from_dict(item))
            else:
                raise TypeError("steps must be strings or step dicts")
        now = _utcnow()
        plan = Plan(
            plan_id=uuid4().hex,
            title=title.strip(),
            objective=objective.strip(),
            status=PlanStatus.DRAFT,
            steps=tuple(parsed),
            created_at=now,
            updated_at=now,
        )
        self.store.save(plan)
        return plan

    def _require(self, plan_id: str, expected: PlanStatus) -> Plan:
        plan = self.store.get(plan_id)
        if plan is None:
            raise PlanError(f"plan not found: {plan_id}")
        if plan.status is not expected:
            raise PlanError(
                f"plan {plan_id} is {plan.status.value}, expected {expected.value}"
            )
        return plan

    def _save(self, plan: Plan) -> Plan:
        updated = Plan(
            plan_id=plan.plan_id,
            title=plan.title,
            objective=plan.objective,
            status=plan.status,
            steps=plan.steps,
            feedback=plan.feedback,
            confirmed_steps=plan.confirmed_steps,
            created_at=plan.created_at,
            updated_at=_utcnow(),
        )
        self.store.save(updated)
        return updated

    def propose(self, plan_id: str) -> Plan:
        plan = self._require(plan_id, PlanStatus.DRAFT)
        return self._save(Plan(
            plan_id=plan.plan_id,
            title=plan.title,
            objective=plan.objective,
            status=PlanStatus.PROPOSED,
            steps=plan.steps,
            feedback="",
            confirmed_steps=(),
            created_at=plan.created_at,
        ))

    def approve(self, plan_id: str) -> Plan:
        plan = self._require(plan_id, PlanStatus.PROPOSED)
        return self._save(Plan(
            plan_id=plan.plan_id,
            title=plan.title,
            objective=plan.objective,
            status=PlanStatus.APPROVED,
            steps=plan.steps,
            created_at=plan.created_at,
        ))

    def reject(self, plan_id: str, feedback: str) -> Plan:
        plan = self._require(plan_id, PlanStatus.PROPOSED)
        if not isinstance(feedback, str) or not feedback.strip():
            raise PlanError("rejection feedback must be a non-empty string")
        return self._save(Plan(
            plan_id=plan.plan_id,
            title=plan.title,
            objective=plan.objective,
            status=PlanStatus.DRAFT,
            steps=plan.steps,
            feedback=feedback.strip(),
            created_at=plan.created_at,
        ))

    def revise(
        self,
        plan_id: str,
        steps: Sequence[str | dict[str, Any]],
    ) -> Plan:
        plan = self.store.get(plan_id)
        if plan is None:
            raise PlanError(f"plan not found: {plan_id}")
        if not steps:
            raise PlanError("a plan must contain at least one step")
        if len(steps) > MAX_STEPS:
            raise PlanError(f"a plan cannot exceed {MAX_STEPS} steps")
        parsed: list[PlanStep] = []
        for item in steps:
            if isinstance(item, str):
                parsed.append(PlanStep(title=item))
            elif isinstance(item, dict):
                parsed.append(PlanStep.from_dict(item))
            else:
                raise TypeError("steps must be strings or step dicts")
        return self._save(Plan(
            plan_id=plan.plan_id,
            title=plan.title,
            objective=plan.objective,
            status=PlanStatus.DRAFT,
            steps=tuple(parsed),
            feedback=plan.feedback,
            created_at=plan.created_at,
        ))

    def confirm_steps(self, plan_id: str, step_titles: Iterable[str]) -> Plan:
        """Host-confirm high-risk steps before execution."""
        plan = self._require(plan_id, PlanStatus.APPROVED)
        available = {step.title for step in plan.steps}
        titles = [title for title in step_titles if title in available]
        return self._save(Plan(
            plan_id=plan.plan_id,
            title=plan.title,
            objective=plan.objective,
            status=plan.status,
            steps=plan.steps,
            feedback=plan.feedback,
            confirmed_steps=tuple(
                dict.fromkeys((*plan.confirmed_steps, *titles))
            ),
            created_at=plan.created_at,
        ))

    # ── execution ──

    def execute(
        self,
        plan_id: str,
        runner: StepRunner,
    ) -> Plan:
        """Execute an approved plan step by step.

        Steps requiring approval that were not confirmed are marked blocked
        (never executed). A runner exception marks the step failed and stops
        execution; the plan is returned in its partially executed state.
        """
        plan = self._require(plan_id, PlanStatus.APPROVED)
        if not callable(runner):
            raise TypeError("runner must be callable")
        completed: list[PlanStep] = []
        stopped = False
        for step in plan.steps:
            if stopped:
                completed.append(step)
                continue
            if step.requires_approval and step.title not in plan.confirmed_steps:
                completed.append(PlanStep(
                    title=step.title,
                    description=step.description,
                    status=PlanStepStatus.BLOCKED,
                    requires_approval=True,
                    result="需要宿主确认后才能执行",
                ))
                continue
            in_progress = PlanStep(
                title=step.title,
                description=step.description,
                status=PlanStepStatus.IN_PROGRESS,
                requires_approval=step.requires_approval,
            )
            try:
                result = runner(plan, in_progress)
                completed.append(PlanStep(
                    title=step.title,
                    description=step.description,
                    status=PlanStepStatus.COMPLETED,
                    requires_approval=step.requires_approval,
                    result=result or "",
                ))
            except Exception as error:
                completed.append(PlanStep(
                    title=step.title,
                    description=step.description,
                    status=PlanStepStatus.FAILED,
                    requires_approval=step.requires_approval,
                    result=f"{type(error).__name__}: {error}",
                ))
                stopped = True
        status = (
            PlanStatus.EXECUTED
            if not stopped
            else PlanStatus.APPROVED
        )
        return self._save(Plan(
            plan_id=plan.plan_id,
            title=plan.title,
            objective=plan.objective,
            status=status,
            steps=tuple(completed),
            feedback=plan.feedback,
            confirmed_steps=plan.confirmed_steps,
            created_at=plan.created_at,
        ))
