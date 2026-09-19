"""Subagent delegation: a model-visible tool that forks a bounded child task.

Mirrors the deepseek-harness subagent capability: the main agent may delegate
a self-contained subtask to a child agent with its own working context and
receive a summarized result, instead of burning main-context tokens on
exploration it does not need to retain.

The executor seam keeps this decoupled from any concrete model/runtime:
``SubagentExecutor`` is the backend contract (in-process driver, CLI wrapper,
remote service), and ``InProcessSubagentExecutor`` adapts a plain callable so
the host decides how a subtask is actually answered. The ``SubagentPool``
enforces depth and concurrency bounds so a misbehaving model cannot fork
unboundedly.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, overload
from uuid import uuid4

from .tools import AgentTool, ToolUpdateCallback

#: Default guardrails mirroring a small local deployment.
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MAX_DEPTH = 3
DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class SubagentRequest:
    """One delegation: the task text plus scoped context the host may use."""

    task: str
    context: Mapping[str, Any] = field(default_factory=dict, compare=False)
    request_id: str = field(default_factory=lambda: uuid4().hex)
    depth: int = 0


@dataclass(frozen=True, slots=True)
class SubagentResult:
    """The child's final answer, summarized for the parent context."""

    output: str
    request_id: str = ""
    is_error: bool = False
    error: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)


class SubagentExecutor(ABC):
    """Contract for any backend that can answer a delegated subtask."""

    @abstractmethod
    def run(self, request: SubagentRequest) -> SubagentResult:
        """Execute one subtask and return its summarized result."""
        raise NotImplementedError


class InProcessSubagentExecutor(SubagentExecutor):
    """Adapter that runs subtasks through a host-supplied callable.

    The callable receives the request and returns either a ``SubagentResult``
    or a plain string (treated as a successful output).
    """

    def __init__(self, runner: Callable[[SubagentRequest], Any]):
        if not callable(runner):
            raise TypeError("runner must be callable")
        self._runner = runner

    def run(self, request: SubagentRequest) -> SubagentResult:
        output = self._runner(request)
        if isinstance(output, SubagentResult):
            return output
        if isinstance(output, str):
            return SubagentResult(output=output, request_id=request.request_id)
        raise TypeError(
            "subagent runner must return a SubagentResult or a string"
        )


class SubagentPool:
    """Bounded pool that executes delegated subtasks with guardrails.

    Enforces: max concurrency (thread semaphore), max nesting depth, and an
    optional wall-clock timeout per subtask. A ``depth`` beyond the limit or a
    failed execution returns an error result instead of raising into the loop.
    """

    def __init__(
        self,
        executor: SubagentExecutor,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_depth: int = DEFAULT_MAX_DEPTH,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if isinstance(max_concurrency, bool) or max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if isinstance(max_depth, bool) or max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive or None")
        if not isinstance(executor, SubagentExecutor):
            raise TypeError("executor must be a SubagentExecutor")
        self.executor = executor
        self.max_concurrency = max_concurrency
        self.max_depth = max_depth
        self.timeout_seconds = timeout_seconds
        self._semaphore = threading.Semaphore(max_concurrency)
        self._active = 0
        self._lock = threading.Lock()

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    def run(self, request: SubagentRequest) -> SubagentResult:
        """Run one subtask through the executor with guardrails applied."""
        if request.depth > self.max_depth:
            return SubagentResult(
                output="",
                request_id=request.request_id,
                is_error=True,
                error=f"subagent depth {request.depth} exceeds limit {self.max_depth}",
            )
        with self._semaphore:
            with self._lock:
                self._active += 1
            try:
                if self.timeout_seconds is None:
                    return self.executor.run(request)
                return self._run_with_timeout(request)
            finally:
                with self._lock:
                    self._active -= 1

    def _run_with_timeout(self, request: SubagentRequest) -> SubagentResult:
        result_box: list[SubagentResult] = []
        error_box: list[BaseException] = []

        def _work() -> None:
            try:
                result_box.append(self.executor.run(request))
            except BaseException as error:  # captured, reported as error result
                error_box.append(error)

        thread = threading.Thread(
            target=_work,
            name=f"subagent-{request.request_id[:8]}",
            daemon=True,
        )
        thread.start()
        thread.join(timeout=self.timeout_seconds)
        if thread.is_alive():
            return SubagentResult(
                output="",
                request_id=request.request_id,
                is_error=True,
                error=f"subagent timed out after {self.timeout_seconds}s",
            )
        if error_box:
            error = error_box[0]
            return SubagentResult(
                output="",
                request_id=request.request_id,
                is_error=True,
                error=f"{type(error).__name__}: {error}",
            )
        return result_box[0]


def subagent_delegate_tool(
    pool: SubagentPool,
    *,
    description: str = (
        "把一段自包含的子任务委托给一个独立的子代理执行，并返回其最终结论。"
        "适用于需要独立探索、与主对话上下文无关的调研或计算。"
    ),
) -> AgentTool:
    """Build the model-visible ``subagent_delegate`` tool backed by a pool."""
    if not isinstance(pool, SubagentPool):
        raise TypeError("pool must be a SubagentPool")

    @overload
    def execute(
        call_id: str,
        arguments: Mapping[str, Any],
        abort_event: threading.Event,
        on_update: Optional[ToolUpdateCallback] = None,
    ) -> dict[str, Any]: ...

    @overload
    def execute(
        *,
        task: str,
        context: Optional[dict[str, Any]] = None,
        depth: int = 0,
    ) -> dict[str, Any]: ...

    def execute(
        call_id: str = "",
        arguments: Optional[Mapping[str, Any]] = None,
        abort_event: Optional[threading.Event] = None,
        on_update: Optional[ToolUpdateCallback] = None,
        *,
        task: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        depth: int = 0,
    ) -> dict[str, Any]:
        del call_id, on_update
        resolved_task: Any = task
        resolved_context: Any = context
        resolved_depth: Any = depth
        if arguments is not None:
            resolved_task = arguments.get("task")
            resolved_context = arguments.get("context")
            resolved_depth = arguments.get("depth", 0)
        if abort_event is not None and abort_event.is_set():
            return {"error": "subagent delegation aborted"}
        if not isinstance(resolved_task, str) or not resolved_task.strip():
            return {"error": "task must be a non-empty string"}
        request = SubagentRequest(
            task=resolved_task,
            context=dict(resolved_context or {}),
            depth=int(resolved_depth or 0),
        )
        result = pool.run(request)
        payload: dict[str, Any] = {"output": result.output}
        if result.is_error:
            payload["error"] = result.error or "subagent failed"
        if result.metadata:
            payload["metadata"] = dict(result.metadata)
        return payload

    return AgentTool(
        name="subagent_delegate",
        description=description,
        execute=execute,
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "自包含的子任务描述，必须能独立完成",
                },
                "context": {
                    "type": "object",
                    "description": "传给子代理的可选结构化上下文",
                },
                "depth": {
                    "type": "integer",
                    "description": "嵌套深度（由池强制上限，通常无需设置）",
                    "minimum": 0,
                },
            },
            "required": ["task"],
        },
        risk="low",
        read_only=True,
        auto_approval_allowed=True,
        execution_mode="sequential",
    )
