"""LangGraph-backed orchestration for collaborative ArtPM tasks.

The existing :mod:`engine` remains the authority for declarative workflows,
skill allowlists, idempotency, and the server-side approval policy.  This
module provides the second orchestration layer needed by multi-agent work:
tasks can depend on one another, independent tasks run in a bounded parallel
batch, transient failures are retried, and human approval pauses the graph.

The graph is deliberately generic.  A host supplies a ``task_runner`` so a
specialist agent, a Skill, or an external adapter can execute one task without
giving the graph arbitrary access to the process.  ``MemorySaver`` is used
only as a safe local default; production hosts should inject a durable
LangGraph checkpointer and may also inject ``SessionStore`` for audit entries.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import logging
import math
from pathlib import Path
from typing import Any, Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


logger = logging.getLogger(__name__)

TaskGraphStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "succeeded",
    "failed",
]


class TaskGraphUnavailableError(RuntimeError):
    """Raised when the optional LangGraph runtime cannot be imported."""


class TaskGraphInputError(ValueError):
    """Raised when a task dependency or input reference is invalid."""


class TaskSpec(BaseModel):
    """Strict, serializable contract for one collaborative task.

    ``skill_id`` and ``capability`` are optional because a task may be handled
    by a dedicated agent supplied through ``task_runner``.  When a coordinator
    uses its default Skill adapter both fields are required and checked against
    the existing server-side allowlist.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    agent: str = Field(
        default="default",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    description: str = Field(default="", max_length=4000)
    input_data: dict[str, Any] = Field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    skill_id: str | None = Field(default=None, min_length=1, max_length=128)
    capability: str | None = Field(default=None, min_length=1, max_length=128)
    side_effect: bool = False
    requires_approval: bool = False
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("depends_on", mode="before")
    @classmethod
    def normalize_dependencies(cls, value: Any) -> Any:
        # JSON callers naturally send an array while internal callers may use
        # a tuple. Strict validation still applies to each dependency string.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_dependencies(self) -> TaskSpec:
        if self.id in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("task dependencies must be unique")
        if self.side_effect and not self.requires_approval:
            raise ValueError("side-effect tasks must require approval")
        return self


class TaskGraphState(TypedDict, total=False):
    """JSON-compatible state persisted by LangGraph checkpoints."""

    run_id: str
    conversation_id: str
    turn_id: str
    goal: str
    tasks: list[dict[str, Any]]
    results: dict[str, Any]
    errors: dict[str, str]
    attempts: dict[str, int]
    completed: list[str]
    approved: list[str]
    pending_approval: list[str]
    status: TaskGraphStatus
    max_parallelism: int
    max_retries: int
    trace: list[dict[str, Any]]


TaskRunner = Callable[[TaskSpec, Mapping[str, Any]], Any]


def _langgraph_runtime() -> tuple[Any, Any, Any, Any, Any]:
    """Import LangGraph lazily so offline/native deployments still start."""

    try:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Command, interrupt
    except ImportError as error:  # pragma: no cover - exercised by packaging
        raise TaskGraphUnavailableError(
            "langgraph is required for collaborative task orchestration; "
            "install the project's LangGraph dependency"
        ) from error
    return StateGraph, START, END, MemorySaver, (Command, interrupt)


def _json_value(value: Any, *, path: str = "value") -> Any:
    """Copy a value while rejecting objects that cannot survive a checkpoint."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"{path} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            copied[key] = _json_value(item, path=f"{path}.{key}")
        return copied
    if isinstance(value, (list, tuple)):
        return [
            _json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(f"{path} must contain only JSON-compatible values")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _lookup_reference(reference: str, state: Mapping[str, Any]) -> Any:
    if not reference.startswith("$") or len(reference) == 1:
        return reference
    parts = reference[1:].split(".")
    roots = {
        "goal": state.get("goal", ""),
        "results": state.get("results", {}),
        "tasks": state.get("tasks", []),
        "run_id": state.get("run_id", ""),
        "conversation_id": state.get("conversation_id", ""),
    }
    root = parts.pop(0)
    if root not in roots:
        raise TaskGraphInputError(f"unsupported task input reference: {reference}")
    current: Any = roots[root]
    for part in parts:
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise TaskGraphInputError(
                    f"task input reference is out of range: {reference}"
                )
            current = current[index]
        else:
            raise TaskGraphInputError(f"missing task input reference: {reference}")
    return current


def _resolve_input(value: Any, state: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return _lookup_reference(value, state)
    if isinstance(value, Mapping):
        return {
            str(key): _resolve_input(item, state) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_resolve_input(item, state) for item in value]
    return value


class LangGraphTaskOrchestrator:
    """Compile and run a dependency-aware collaborative task graph."""

    def __init__(
        self,
        task_runner: TaskRunner,
        *,
        checkpointer: Any = None,
        session_store: Any = None,
        max_parallelism: int = 4,
        max_retries: int = 2,
    ) -> None:
        if not callable(task_runner):
            raise TypeError("task_runner must be callable")
        if isinstance(max_parallelism, bool) or not 1 <= max_parallelism <= 32:
            raise ValueError("max_parallelism must be between 1 and 32")
        if isinstance(max_retries, bool) or not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be between 0 and 10")

        StateGraph, start, end, MemorySaver, command_symbols = _langgraph_runtime()
        self._command_type, self._interrupt = command_symbols
        self.task_runner = task_runner
        self.session_store = session_store
        self.max_parallelism = max_parallelism
        self.max_retries = max_retries
        self.checkpointer = checkpointer or MemorySaver()

        graph = StateGraph(TaskGraphState)
        graph.add_node("prepare", self._prepare)
        graph.add_node("dispatch", self._dispatch)
        graph.add_node("approval", self._approval)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(start, "prepare")
        graph.add_edge("prepare", "dispatch")
        graph.add_conditional_edges(
            "dispatch",
            self._route_after_dispatch,
            {
                "dispatch": "dispatch",
                "approval": "approval",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "approval",
            self._route_after_approval,
            {"dispatch": "dispatch", "finalize": "finalize"},
        )
        graph.add_edge("finalize", end)
        self.graph = graph.compile(checkpointer=self.checkpointer)

    @staticmethod
    def _normalize_tasks(tasks: Sequence[TaskSpec | Mapping[str, Any]]) -> list[TaskSpec]:
        normalized: list[TaskSpec] = []
        seen: set[str] = set()
        for raw in tasks:
            task = raw if isinstance(raw, TaskSpec) else TaskSpec.model_validate(raw)
            _json_value(task.input_data, path=f"tasks.{task.id}.input")
            _json_value(task.metadata, path=f"tasks.{task.id}.metadata")
            if task.id in seen:
                raise TaskGraphInputError(f"duplicate task id: {task.id}")
            seen.add(task.id)
            normalized.append(task)
        if not normalized:
            raise TaskGraphInputError("at least one task is required")
        task_ids = {task.id for task in normalized}
        for task in normalized:
            missing = sorted(set(task.depends_on) - task_ids)
            if missing:
                raise TaskGraphInputError(
                    f"task {task.id} depends on unknown task(s): {', '.join(missing)}"
                )
        # Kahn's algorithm catches cycles before any task is executed.
        remaining = {task.id: set(task.depends_on) for task in normalized}
        resolved: set[str] = set()
        while remaining:
            ready = [task_id for task_id, deps in remaining.items() if not deps]
            if not ready:
                cycle = ", ".join(sorted(remaining))
                raise TaskGraphInputError(f"task dependency cycle detected: {cycle}")
            for task_id in ready:
                resolved.add(task_id)
                remaining.pop(task_id)
            for deps in remaining.values():
                deps.difference_update(ready)
        return normalized

    def _append_audit(self, state: Mapping[str, Any], event: str, payload: Any) -> None:
        if self.session_store is None:
            return
        conversation_id = str(state.get("conversation_id") or "").strip()
        if not conversation_id:
            return
        try:
            self.session_store.append(
                conversation_id,
                f"task_graph.{event}",
                run_id=str(state.get("run_id") or "task-graph"),
                turn_id=str(state.get("turn_id") or state.get("run_id") or "task"),
                payload=_json_value(payload, path="audit.payload"),
            )
        except Exception:  # pragma: no cover - audit must not break execution
            logger.exception("Unable to append task graph audit event")

    def _prepare(self, state: TaskGraphState) -> dict[str, Any]:
        tasks = self._normalize_tasks(state.get("tasks", []))
        normalized = [task.model_dump(mode="json") for task in tasks]
        run_id = str(state.get("run_id") or uuid4().hex)
        goal = str(state.get("goal") or "").strip()
        if not goal:
            raise TaskGraphInputError("goal must be a non-empty string")
        update: dict[str, Any] = {
            "run_id": run_id,
            "tasks": normalized,
            "results": {},
            "errors": {},
            "attempts": {},
            "completed": [],
            "approved": [],
            "pending_approval": [],
            "status": "running",
            "max_parallelism": int(
                state.get("max_parallelism") or self.max_parallelism
            ),
            "max_retries": int(
                self.max_retries
                if state.get("max_retries") is None
                else state.get("max_retries")
            ),
            "trace": [
                {
                    "event": "graph.started",
                    "run_id": run_id,
                    "at": _utc_now(),
                }
            ],
        }
        if update["max_parallelism"] < 1 or update["max_parallelism"] > 32:
            raise TaskGraphInputError("max_parallelism must be between 1 and 32")
        if update["max_retries"] < 0 or update["max_retries"] > 10:
            raise TaskGraphInputError("max_retries must be between 0 and 10")
        self._append_audit(
            {**state, **update},
            "started",
            {"goal": goal, "task_ids": [task.id for task in tasks]},
        )
        update["goal"] = goal
        return update

    @staticmethod
    def _task_from_state(raw: Mapping[str, Any]) -> TaskSpec:
        return TaskSpec.model_validate(raw)

    def _ready_tasks(self, state: Mapping[str, Any]) -> tuple[list[TaskSpec], list[TaskSpec]]:
        completed = set(state.get("completed", []))
        approved = set(state.get("approved", []))
        tasks = [self._task_from_state(raw) for raw in state.get("tasks", [])]
        pending_approval: list[TaskSpec] = []
        ready: list[TaskSpec] = []
        for task in tasks:
            if task.id in completed:
                continue
            if not all(dependency in completed for dependency in task.depends_on):
                continue
            if task.requires_approval and task.id not in approved:
                pending_approval.append(task)
            else:
                ready.append(task)
        return ready, pending_approval

    def _execute_one(
        self,
        task: TaskSpec,
        state: Mapping[str, Any],
        prior_attempts: int,
    ) -> tuple[str, Any, int, str | None, list[dict[str, Any]]]:
        max_attempts = task.max_attempts or (int(state.get("max_retries", 0)) + 1)
        attempts = prior_attempts
        events: list[dict[str, Any]] = []
        while attempts < max_attempts:
            attempts += 1
            started = _utc_now()
            try:
                resolved_input = _json_value(
                    _resolve_input(task.input_data, state),
                    path=f"tasks.{task.id}.input",
                )
                context = {
                    "run_id": str(state.get("run_id") or ""),
                    "conversation_id": str(state.get("conversation_id") or ""),
                    "goal": state.get("goal", ""),
                    "task_id": task.id,
                    "agent": task.agent,
                    "attempt": attempts,
                    "inputs": resolved_input,
                    "results": state.get("results", {}),
                    "approved": task.id in set(state.get("approved", [])),
                    "metadata": task.metadata,
                }
                result = _json_value(
                    self.task_runner(task, context),
                    path=f"tasks.{task.id}.result",
                )
                if isinstance(result, Mapping) and result.get("success") is False:
                    raise RuntimeError(str(result.get("error") or "task reported failure"))
                events.append(
                    {
                        "event": "task.succeeded",
                        "task_id": task.id,
                        "attempt": attempts,
                        "at": started,
                    }
                )
                return task.id, result, attempts, None, events
            except Exception as error:  # task failures are graph data, not crashes
                message = str(error).strip() or error.__class__.__name__
                message = message[:2000]
                events.append(
                    {
                        "event": "task.failed",
                        "task_id": task.id,
                        "attempt": attempts,
                        "error": message,
                        "at": started,
                    }
                )
                if attempts >= max_attempts:
                    return task.id, None, attempts, message, events
        return task.id, None, attempts, "task attempts exhausted", events

    def _dispatch(self, state: TaskGraphState) -> dict[str, Any]:
        ready, pending_approval = self._ready_tasks(state)
        update: dict[str, Any] = {
            "pending_approval": [task.id for task in pending_approval],
        }
        if pending_approval:
            update["status"] = "waiting_approval"
        if not ready:
            all_done = len(state.get("completed", [])) == len(state.get("tasks", []))
            if not pending_approval and not all_done:
                blocked = {
                    task.id: "dependencies cannot be satisfied"
                    for task in (
                        self._task_from_state(raw) for raw in state.get("tasks", [])
                    )
                    if task.id not in set(state.get("completed", []))
                }
                update["errors"] = {**state.get("errors", {}), **blocked}
                update["status"] = "failed"
            return update

        prior_attempts = state.get("attempts", {})
        results: dict[str, Any] = dict(state.get("results", {}))
        errors: dict[str, str] = dict(state.get("errors", {}))
        attempts: dict[str, int] = dict(prior_attempts)
        completed = list(state.get("completed", []))
        trace = list(state.get("trace", []))

        worker_count = min(int(state.get("max_parallelism", self.max_parallelism)), len(ready))
        if worker_count == 1:
            outcomes = [
                self._execute_one(task, state, int(prior_attempts.get(task.id, 0)))
                for task in ready
            ]
        else:
            outcomes_by_id: dict[str, tuple[str, Any, int, str | None, list[dict[str, Any]]]] = {}
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="artpm-task",
            ) as pool:
                futures = {
                    pool.submit(
                        self._execute_one,
                        task,
                        state,
                        int(prior_attempts.get(task.id, 0)),
                    ): task.id
                    for task in ready
                }
                for future in as_completed(futures):
                    outcome = future.result()
                    outcomes_by_id[outcome[0]] = outcome
            outcomes = [outcomes_by_id[task.id] for task in ready]

        for task_id, result, task_attempts, error, events in outcomes:
            attempts[task_id] = task_attempts
            trace.extend(events)
            if error is not None:
                errors[task_id] = error
            else:
                results[task_id] = result
                if task_id not in completed:
                    completed.append(task_id)

        update.update(
            {
                "results": results,
                "errors": errors,
                "attempts": attempts,
                "completed": completed,
                "trace": trace,
                "status": "failed" if errors else "running",
            }
        )
        self._append_audit(
            {**state, **update},
            "batch.completed",
            {
                "task_ids": [task.id for task in ready],
                "completed": completed,
                "errors": errors,
            },
        )
        return update

    @staticmethod
    def _route_after_dispatch(state: TaskGraphState) -> str:
        if state.get("status") == "failed":
            return "finalize"
        if state.get("pending_approval"):
            return "approval"
        if len(state.get("completed", [])) >= len(state.get("tasks", [])):
            return "finalize"
        return "dispatch"

    def _approval(self, state: TaskGraphState) -> dict[str, Any]:
        pending = list(state.get("pending_approval", []))
        if not pending:
            return {"status": "running"}
        decision = self._interrupt(
            {
                "type": "task_graph_approval",
                "run_id": state.get("run_id"),
                "task_ids": pending,
                "message": "Approval is required before side-effect tasks can run.",
            }
        )
        approved, rejected = self._parse_approval(decision, pending)
        if rejected:
            errors = dict(state.get("errors", {}))
            for task_id in rejected:
                errors[task_id] = "task approval rejected"
            update = {
                "approved": sorted(set(state.get("approved", [])) | set(approved)),
                "pending_approval": [],
                "errors": errors,
                "status": "failed",
            }
        else:
            update = {
                "approved": sorted(set(state.get("approved", [])) | set(approved)),
                "pending_approval": [],
                "status": "running",
            }
        trace = list(state.get("trace", []))
        trace.append(
            {
                "event": "approval.decided",
                "task_ids": pending,
                "approved": approved,
                "rejected": rejected,
                "at": _utc_now(),
            }
        )
        update["trace"] = trace
        self._append_audit(
            {**state, **update},
            "approval.decided",
            {"task_ids": pending, "approved": approved, "rejected": rejected},
        )
        return update

    @staticmethod
    def _parse_approval(
        decision: Any,
        pending: Sequence[str],
    ) -> tuple[list[str], list[str]]:
        pending_set = set(pending)
        if isinstance(decision, bool):
            return (list(pending), []) if decision else ([], list(pending))
        if isinstance(decision, Mapping):
            approved_value = decision.get("approved", [])
            rejected_value = decision.get("rejected", [])
            if isinstance(approved_value, bool):
                approved_value = pending if approved_value else []
            if isinstance(rejected_value, bool):
                rejected_value = pending if rejected_value else []
            if isinstance(approved_value, str):
                approved_value = [approved_value]
            if isinstance(rejected_value, str):
                rejected_value = [rejected_value]
            if not isinstance(approved_value, Sequence) or isinstance(
                approved_value, (bytes, bytearray)
            ):
                raise TaskGraphInputError("approval.approved must be a task id list")
            if not isinstance(rejected_value, Sequence) or isinstance(
                rejected_value, (bytes, bytearray)
            ):
                raise TaskGraphInputError("approval.rejected must be a task id list")
            approved = [str(task_id) for task_id in approved_value]
            rejected = [str(task_id) for task_id in rejected_value]
            unknown = (set(approved) | set(rejected)) - pending_set
            if unknown:
                raise TaskGraphInputError(
                    f"approval references unknown pending task(s): {', '.join(sorted(unknown))}"
                )
            overlap = set(approved) & set(rejected)
            if overlap:
                raise TaskGraphInputError(
                    f"approval contains both decisions for: {', '.join(sorted(overlap))}"
                )
            if set(approved) | set(rejected) != pending_set:
                raise TaskGraphInputError("approval must decide every pending task")
            return approved, rejected
        raise TaskGraphInputError("approval decision must be a boolean or mapping")

    @staticmethod
    def _route_after_approval(state: TaskGraphState) -> str:
        return "finalize" if state.get("status") == "failed" else "dispatch"

    def _finalize(self, state: TaskGraphState) -> dict[str, Any]:
        status: TaskGraphStatus = (
            "failed" if state.get("errors") else "succeeded"
        )
        trace = list(state.get("trace", []))
        trace.append(
            {
                "event": "graph.finished",
                "status": status,
                "at": _utc_now(),
            }
        )
        update = {"status": status, "trace": trace, "pending_approval": []}
        self._append_audit(
            {**state, **update},
            "finished",
            {"status": status, "completed": state.get("completed", []), "errors": state.get("errors", {})},
        )
        return update

    @staticmethod
    def _config(thread_id: str) -> dict[str, dict[str, str]]:
        thread_id = str(thread_id).strip()
        if not thread_id:
            raise ValueError("thread_id must be a non-empty string")
        return {"configurable": {"thread_id": thread_id}}

    def invoke(
        self,
        goal: str,
        tasks: Sequence[TaskSpec | Mapping[str, Any]],
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        max_parallelism: int | None = None,
        max_retries: int | None = None,
    ) -> TaskGraphState:
        """Start a graph run and return its current state.

        A run that reaches a human approval node returns with
        ``status == 'waiting_approval'``.  Call :meth:`resume` with the same
        ``thread_id`` to continue it.
        """

        resolved_thread_id = str(thread_id or run_id or uuid4().hex)
        initial: TaskGraphState = {
            "run_id": str(run_id or resolved_thread_id),
            "conversation_id": str(conversation_id or ""),
            "turn_id": str(turn_id or resolved_thread_id),
            "goal": goal,
            "tasks": [
                (task.model_dump(mode="json") if isinstance(task, TaskSpec) else dict(task))
                for task in tasks
            ],
        }
        if max_parallelism is not None:
            initial["max_parallelism"] = max_parallelism
        if max_retries is not None:
            initial["max_retries"] = max_retries
        result = self.graph.invoke(initial, config=self._config(resolved_thread_id))
        return result

    def resume(self, thread_id: str, decision: Any) -> TaskGraphState:
        """Resume an interrupted approval node using LangGraph ``Command``."""

        command = self._command_type(resume=decision)
        return self.graph.invoke(command, config=self._config(thread_id))

    def snapshot(self, thread_id: str) -> TaskGraphState:
        """Read the latest checkpoint without advancing the graph."""

        state = self.graph.get_state(self._config(thread_id))
        values = getattr(state, "values", None)
        if not isinstance(values, Mapping):
            raise RuntimeError("LangGraph checkpoint does not contain a state mapping")
        return dict(values)


def make_sqlite_session_audit(db_path: str | Path) -> Any:
    """Create the project's append-only session audit adapter for a DB path."""

    from artpm_agent.memory.session_store import SessionStore

    return SessionStore(db_path)


__all__ = [
    "LangGraphTaskOrchestrator",
    "TaskGraphInputError",
    "TaskGraphState",
    "TaskGraphStatus",
    "TaskGraphUnavailableError",
    "TaskRunner",
    "TaskSpec",
    "make_sqlite_session_audit",
]
