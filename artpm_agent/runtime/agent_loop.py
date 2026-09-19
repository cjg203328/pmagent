"""Low-level multi-turn agent loop with structured tool execution.

This module mirrors Pi's separation between the small provider-neutral loop and
the higher-level application harness. It does not know about Streamlit, model
vendors, databases, or ArtPM business rules.
"""

from __future__ import annotations

import inspect
import math
from collections import deque
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Event, RLock
from time import monotonic
from types import MappingProxyType
from typing import Any, Optional
from uuid import uuid4

from .events import AgentEvent, AgentEventType, AgentMessage
from .pipeline import (
    DEFAULT_DETAILS_MAX_CHARS,
    DEFAULT_SPILL_MAX_CHARS,
    DEFAULT_SPILL_TTL_SECONDS,
    ToolExecutionPipeline,
    spill_scope_values,
    spill_tool_result,
)
from .tools import (
    AgentTool,
    BeforeToolCallDecision,
    ToolCall,
    ToolExecutionMode,
    ToolRegistry,
    ToolResult,
    normalize_tool_result,
)

TurnProvider = Callable[
    [tuple[AgentMessage, ...], tuple[dict[str, Any], ...], Mapping[str, Any]],
    "AssistantTurn",
]
ContextTransform = Callable[
    [tuple[AgentMessage, ...], Mapping[str, Any]],
    Iterable[AgentMessage],
]
BeforeToolCallHook = Callable[
    [ToolCall, AgentTool, Mapping[str, Any], Mapping[str, Any]],
    Optional[BeforeToolCallDecision],
]
AfterToolCallHook = Callable[
    [ToolCall, AgentTool, ToolResult, Mapping[str, Any]],
    Optional[ToolResult],
]
ShouldStopAfterTurn = Callable[
    ["AssistantTurn", "ToolBatchResult", tuple[AgentMessage, ...]],
    bool,
]


class AgentLoopBusyError(RuntimeError):
    """Raised when a loop instance receives overlapping runs."""


class AgentLoopAbortedError(RuntimeError):
    """Raised when cooperative cancellation is observed."""


class AgentLoopLimitError(RuntimeError):
    """Raised before another model turn would exceed the configured limit."""


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    """Provider-neutral assistant output for one model turn."""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("assistant turn content must be a string")
        calls = tuple(self.tool_calls)
        if any(not isinstance(call, ToolCall) for call in calls):
            raise TypeError("assistant turn tool_calls must contain ToolCall values")
        call_ids = [call.id for call in calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("assistant turn tool call ids must be unique")
        if not self.content.strip() and not calls:
            raise ValueError("assistant turn must contain text or tool calls")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("assistant turn metadata must be a mapping")
        object.__setattr__(self, "tool_calls", calls)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    """One finalized call and the transcript message sent to the model."""

    call: ToolCall
    result: ToolResult
    message: AgentMessage

    def to_dict(self) -> dict[str, Any]:
        return {
            "call": self.call.to_dict(),
            "result": self.result.to_dict(),
            "message_id": self.message.id,
        }


@dataclass(frozen=True, slots=True)
class ToolBatchResult:
    records: tuple[ToolExecutionRecord, ...] = ()

    @property
    def terminate(self) -> bool:
        return bool(self.records) and not any(
            record.result.is_error for record in self.records
        ) and any(record.result.terminate for record in self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "terminate": self.terminate,
            "records": [record.to_dict() for record in self.records],
        }


@dataclass(frozen=True, slots=True)
class _PreparedToolCall:
    index: int
    call: ToolCall
    tool: Optional[AgentTool] = None
    arguments: Mapping[str, Any] = field(default_factory=dict, compare=False)
    immediate_result: Optional[ToolResult] = None


class ToolExecutor:
    """Validate, authorize, execute, and normalize a batch of tool calls."""

    _HOST_APPROVAL_FIELDS = frozenset({"approved", "confirmation_token"})

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        execution_mode: ToolExecutionMode = "parallel",
        before_tool_call: Optional[BeforeToolCallHook] = None,
        after_tool_call: Optional[AfterToolCallHook] = None,
        max_workers: int = 8,
        tool_timeout_seconds: Optional[float] = 120.0,
        result_max_chars: int = DEFAULT_SPILL_MAX_CHARS,
        result_details_max_chars: int = DEFAULT_DETAILS_MAX_CHARS,
        spill_dir: Any = None,
        spill_ttl_seconds: Optional[float] = DEFAULT_SPILL_TTL_SECONDS,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        if execution_mode not in {"parallel", "sequential"}:
            raise ValueError("tool execution mode is invalid")
        if before_tool_call is not None and not callable(before_tool_call):
            raise TypeError("before_tool_call must be callable")
        if after_tool_call is not None and not callable(after_tool_call):
            raise TypeError("after_tool_call must be callable")
        if isinstance(max_workers, bool) or not isinstance(max_workers, int):
            raise TypeError("max_workers must be an integer")
        if not 1 <= max_workers <= 64:
            raise ValueError("max_workers must be between 1 and 64")
        if tool_timeout_seconds is not None:
            if isinstance(tool_timeout_seconds, bool) or not isinstance(
                tool_timeout_seconds,
                (int, float),
            ):
                raise TypeError("tool_timeout_seconds must be a number or None")
            if not math.isfinite(float(tool_timeout_seconds)) or tool_timeout_seconds <= 0:
                raise ValueError(
                    "tool_timeout_seconds must be finite and greater than zero"
                )
        if isinstance(result_max_chars, bool) or not isinstance(result_max_chars, int):
            raise TypeError("result_max_chars must be an integer")
        if result_max_chars < 512:
            raise ValueError("result_max_chars must be at least 512")
        if isinstance(result_details_max_chars, bool) or not isinstance(
            result_details_max_chars,
            int,
        ):
            raise TypeError("result_details_max_chars must be an integer")
        if result_details_max_chars < 512:
            raise ValueError("result_details_max_chars must be at least 512")
        if spill_ttl_seconds is not None:
            if isinstance(spill_ttl_seconds, bool) or not isinstance(
                spill_ttl_seconds,
                (int, float),
            ):
                raise TypeError("spill_ttl_seconds must be a number or None")
            if not math.isfinite(float(spill_ttl_seconds)) or spill_ttl_seconds < 0:
                raise ValueError(
                    "spill_ttl_seconds must be finite and non-negative"
                )
        self.registry = registry
        self.execution_mode = execution_mode
        self.before_tool_call = before_tool_call
        self.after_tool_call = after_tool_call
        self.max_workers = max_workers
        self.tool_timeout_seconds = (
            float(tool_timeout_seconds)
            if tool_timeout_seconds is not None
            else None
        )
        self.result_max_chars = result_max_chars
        self.result_details_max_chars = result_details_max_chars
        self.spill_dir = spill_dir
        self.spill_ttl_seconds = (
            float(spill_ttl_seconds)
            if spill_ttl_seconds is not None
            else None
        )

    def execute_batch(
        self,
        calls: Sequence[ToolCall],
        *,
        run_id: str,
        turn_id: str,
        context: Mapping[str, Any],
        abort_event: Event,
        allow_side_effects: bool = True,
    ) -> Generator[AgentEvent, None, ToolBatchResult]:
        prepared: list[_PreparedToolCall] = []
        finalized: list[Optional[ToolResult]] = [None] * len(calls)

        for index, call in enumerate(calls):
            if abort_event.is_set():
                raise AgentLoopAbortedError("agent loop aborted")
            yield self._event(
                AgentEventType.TOOL_EXECUTION_START,
                call,
                run_id,
                turn_id,
            )
            if abort_event.is_set():
                raise AgentLoopAbortedError("agent loop aborted")
            item = self._prepare(
                index,
                call,
                context,
                abort_event,
                allow_side_effects=allow_side_effects,
            )
            prepared.append(item)
            if item.immediate_result is not None:
                finalized[index] = item.immediate_result
                yield self._event(
                    AgentEventType.TOOL_EXECUTION_END,
                    call,
                    run_id,
                    turn_id,
                    result=item.immediate_result,
                )

        executable = [item for item in prepared if item.immediate_result is None]
        force_sequential = self.execution_mode == "sequential" or any(
            item.tool is not None and item.tool.execution_mode == "sequential"
            for item in executable
        )
        worker_count = (
            1
            if force_sequential
            else min(self.max_workers, max(1, len(executable)))
        )
        if executable:
            updates: Queue[tuple[str, int, ToolResult]] = Queue()
            pending = deque(executable)
            active: dict[int, tuple[Any, Event, Event, Optional[float]]] = {}
            timed_out = False
            pool = ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="artpm-tool",
            )

            def submit_available() -> None:
                while pending and len(active) < worker_count and not timed_out:
                    item = pending.popleft()
                    tool_abort = Event()
                    delivery_gate = Event()
                    delivery_gate.set()
                    if abort_event.is_set():
                        tool_abort.set()
                    future = pool.submit(
                        self._execute_worker,
                        item,
                        context,
                        tool_abort,
                        delivery_gate,
                        updates,
                    )

                    def publish_worker_exit(
                        _future: Any,
                        *,
                        index: int = item.index,
                    ) -> None:
                        updates.put(
                            ("worker_exit", index, ToolResult.error("unused"))
                        )

                    future.add_done_callback(publish_worker_exit)
                    deadline = (
                        monotonic() + self.tool_timeout_seconds
                        if self.tool_timeout_seconds is not None
                        else None
                    )
                    active[item.index] = (
                        future,
                        tool_abort,
                        delivery_gate,
                        deadline,
                    )

            try:
                submit_available()
                while active or pending:
                    if abort_event.is_set():
                        for (
                            _future,
                            tool_abort,
                            delivery_gate,
                            _deadline,
                        ) in active.values():
                            delivery_gate.clear()
                            tool_abort.set()
                        raise AgentLoopAbortedError("agent loop aborted")

                    deadlines = [
                        deadline
                        for _future, _tool_abort, _delivery_gate, deadline in active.values()
                        if deadline is not None
                    ]
                    wait_timeout = 0.05
                    if deadlines:
                        wait_timeout = min(
                            wait_timeout,
                            max(0.0, min(deadlines) - monotonic()),
                        )
                    try:
                        kind, index, result = updates.get(timeout=wait_timeout)
                    except Empty:
                        kind = ""
                        index = -1
                        result = ToolResult.error("unused")

                    active_entry = active.get(index)
                    event_before_deadline = (
                        active_entry is not None
                        and (
                            active_entry[3] is None
                            or active_entry[3] > monotonic()
                        )
                    )
                    if index in active and event_before_deadline:
                        item = prepared[index]
                        if kind == "update":
                            tool = item.tool
                            if tool is None:
                                raise RuntimeError("tool update has no registered tool")
                            yield self._event(
                                AgentEventType.TOOL_EXECUTION_UPDATE,
                                item.call,
                                run_id,
                                turn_id,
                                result=self._bound_result(tool, result, context),
                            )
                        elif kind == "done":
                            _future, _tool_abort, delivery_gate, _deadline = active.pop(
                                index
                            )
                            delivery_gate.clear()
                            tool = item.tool
                            if tool is None:
                                raise RuntimeError("tool completion has no registered tool")
                            bounded_result = self._bound_result(tool, result, context)
                            finalized[index] = bounded_result
                            yield self._event(
                                AgentEventType.TOOL_EXECUTION_END,
                                item.call,
                                run_id,
                                turn_id,
                                result=bounded_result,
                            )
                        elif kind == "worker_exit":
                            _future, _tool_abort, delivery_gate, _deadline = active.pop(
                                index
                            )
                            delivery_gate.clear()
                            if finalized[index] is None:
                                worker_error = ToolResult.error(
                                    "Tool worker exited without returning a result"
                                )
                                finalized[index] = worker_error
                                yield self._event(
                                    AgentEventType.TOOL_EXECUTION_END,
                                    item.call,
                                    run_id,
                                    turn_id,
                                    result=worker_error,
                                )

                    now = monotonic()
                    expired = [
                        index
                        for index, (
                            _future,
                            _tool_abort,
                            _delivery_gate,
                            deadline,
                        ) in active.items()
                        if (
                            deadline is not None
                            and deadline <= now
                        )
                    ]
                    for index in expired:
                        future, tool_abort, delivery_gate, _deadline = active.pop(index)
                        delivery_gate.clear()
                        tool_abort.set()
                        future.cancel()
                        timeout_result = ToolResult.error(
                            "Tool execution timed out after "
                            f"{self.tool_timeout_seconds:g} seconds"
                        )
                        finalized[index] = timeout_result
                        timed_out = True
                        yield self._event(
                            AgentEventType.TOOL_EXECUTION_END,
                            prepared[index].call,
                            run_id,
                            turn_id,
                            result=timeout_result,
                        )

                    if timed_out:
                        while pending:
                            item = pending.popleft()
                            skipped = ToolResult.error(
                                "Tool execution skipped because another tool "
                                "timed out in the same batch"
                            )
                            finalized[item.index] = skipped
                            yield self._event(
                                AgentEventType.TOOL_EXECUTION_END,
                                item.call,
                                run_id,
                                turn_id,
                                result=skipped,
                            )
                    else:
                        submit_available()
            finally:
                for _future, tool_abort, delivery_gate, _deadline in active.values():
                    delivery_gate.clear()
                    tool_abort.set()
                pool.shutdown(wait=not timed_out and not active, cancel_futures=True)

        records: list[ToolExecutionRecord] = []
        for index, call in enumerate(calls):
            if abort_event.is_set():
                raise AgentLoopAbortedError("agent loop aborted")
            final_result = finalized[index]
            if final_result is None:
                raise RuntimeError(f"tool call did not finalize: {call.name}")
            message = self._result_message(call, final_result)
            yield AgentEvent(
                type=AgentEventType.MESSAGE_START,
                run_id=run_id,
                turn_id=turn_id,
                message=message,
            )
            yield AgentEvent(
                type=AgentEventType.MESSAGE_END,
                run_id=run_id,
                turn_id=turn_id,
                message=message,
            )
            records.append(ToolExecutionRecord(call, final_result, message))
        return ToolBatchResult(tuple(records))

    def _prepare(
        self,
        index: int,
        call: ToolCall,
        context: Mapping[str, Any],
        abort_event: Event,
        *,
        allow_side_effects: bool,
    ) -> _PreparedToolCall:
        tool = self.registry.get(call.name)
        if tool is None:
            return _PreparedToolCall(
                index=index,
                call=call,
                immediate_result=ToolResult.error(f"Tool {call.name} not found"),
            )
        try:
            model_arguments = {
                key: value
                for key, value in call.arguments.items()
                if key not in self._HOST_APPROVAL_FIELDS
            }
            arguments = tool.prepare(model_arguments)
            if not allow_side_effects and not tool.read_only:
                return _PreparedToolCall(
                    index=index,
                    call=call,
                    tool=tool,
                    immediate_result=ToolResult.error(
                        "Side-effecting tools cannot run on the final agent turn"
                    ),
                )
            decision = (
                self.before_tool_call(call, tool, arguments, context)
                if self.before_tool_call is not None
                else None
            )
            if decision is not None and not isinstance(
                decision,
                BeforeToolCallDecision,
            ):
                raise TypeError("before_tool_call returned an invalid decision")
            if abort_event.is_set():
                raise AgentLoopAbortedError("agent loop aborted")
            if decision is not None and decision.block:
                return _PreparedToolCall(
                    index=index,
                    call=call,
                    tool=tool,
                    immediate_result=ToolResult.error(
                        decision.reason or "Tool execution was blocked"
                    ),
                )
            if tool.requires_approval and not (decision and decision.approved):
                return _PreparedToolCall(
                    index=index,
                    call=call,
                    tool=tool,
                    immediate_result=ToolResult.error(
                        f"Tool requires explicit host approval: {tool.name}"
                    ),
                )
            trusted_arguments = dict(arguments)
            if tool.requires_approval:
                trusted_arguments["approved"] = True
            return _PreparedToolCall(
                index=index,
                call=call,
                tool=tool,
                arguments=MappingProxyType(trusted_arguments),
            )
        except AgentLoopAbortedError:
            raise
        except Exception as error:
            return _PreparedToolCall(
                index=index,
                call=call,
                tool=tool,
                immediate_result=ToolResult.error(
                    str(error) or error.__class__.__name__
                ),
            )

    def _execute_worker(
        self,
        prepared: _PreparedToolCall,
        context: Mapping[str, Any],
        abort_event: Event,
        delivery_gate: Event,
        updates: Queue[tuple[str, int, ToolResult]],
    ) -> None:
        tool = prepared.tool
        if tool is None:
            updates.put(("done", prepared.index, ToolResult.error("Tool not found")))
            return

        tool_running = Event()
        tool_running.set()

        def on_update(partial: ToolResult) -> None:
            if (
                delivery_gate.is_set()
                and tool_running.is_set()
                and not abort_event.is_set()
            ):
                normalized = normalize_tool_result(partial)
                if delivery_gate.is_set() and not abort_event.is_set():
                    updates.put(("update", prepared.index, normalized))

        try:
            if abort_event.is_set():
                raise AgentLoopAbortedError("agent loop aborted")
            result = tool.invoke(
                prepared.call.id,
                prepared.arguments,
                abort_event,
                on_update,
            )
        except BaseException as error:  # noqa: BLE001 - worker must always finalize
            result = ToolResult.error(str(error) or error.__class__.__name__)
        finally:
            tool_running.clear()

        if not delivery_gate.is_set():
            return

        if self.after_tool_call is not None and not abort_event.is_set():
            try:
                replacement = self.after_tool_call(
                    prepared.call,
                    tool,
                    result,
                    context,
                )
                if replacement is not None:
                    if not isinstance(replacement, ToolResult):
                        raise TypeError(
                            "after_tool_call must return ToolResult or None"
                        )
                    result = replacement
            except BaseException as error:  # noqa: BLE001 - worker must always finalize
                result = ToolResult.error(str(error) or error.__class__.__name__)
        if not delivery_gate.is_set():
            return
        if delivery_gate.is_set():
            updates.put(("done", prepared.index, result))

    def _bound_result(
        self,
        tool: AgentTool,
        result: ToolResult,
        context: Mapping[str, Any],
    ) -> ToolResult:
        try:
            tenant_id, workspace_id = spill_scope_values(context)
            return spill_tool_result(
                result,
                max_chars=self.result_max_chars,
                details_max_chars=self.result_details_max_chars,
                spill_dir=self.spill_dir,
                tool_name=tool.name,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                ttl_seconds=self.spill_ttl_seconds,
            )
        except Exception as error:
            return ToolResult.error(
                "Tool result could not be safely bounded: "
                f"{str(error) or error.__class__.__name__}"
            )

    def _drain_one(
        self,
        item: _PreparedToolCall,
        updates: Queue[tuple[str, int, ToolResult]],
        finalized: list[Optional[ToolResult]],
        run_id: str,
        turn_id: str,
    ) -> Iterator[AgentEvent]:
        while True:
            kind, index, result = updates.get()
            if index != item.index:
                raise RuntimeError("sequential tool event order was corrupted")
            if kind == "update":
                yield self._event(
                    AgentEventType.TOOL_EXECUTION_UPDATE,
                    item.call,
                    run_id,
                    turn_id,
                    result=result,
                )
                continue
            finalized[index] = result
            yield self._event(
                AgentEventType.TOOL_EXECUTION_END,
                item.call,
                run_id,
                turn_id,
                result=result,
            )
            return

    @staticmethod
    def _event(
        event_type: AgentEventType,
        call: ToolCall,
        run_id: str,
        turn_id: str,
        *,
        result: Optional[ToolResult] = None,
    ) -> AgentEvent:
        return AgentEvent(
            type=event_type,
            run_id=run_id,
            turn_id=turn_id,
            tool_call_id=call.id,
            tool_name=call.name,
            tool_arguments=call.arguments,
            tool_result=result.to_dict() if result is not None else {},
            is_error=result.is_error if result is not None else False,
        )

    @staticmethod
    def _result_message(call: ToolCall, result: ToolResult) -> AgentMessage:
        return AgentMessage(
            role="toolResult",
            content=result.content,
            status="error" if result.is_error else "complete",
            metadata={
                "tool_call_id": call.id,
                "tool_name": call.name,
                "details": dict(result.details),
                "is_error": result.is_error,
                "terminate": result.terminate,
            },
        )


class AgentLoop:
    """Small multi-turn loop that delegates all application policy to hooks."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        *,
        max_turns: int = 8,
        max_tool_calls_per_turn: int = 16,
        max_tool_calls_total: Optional[int] = None,
        max_tool_workers: int = 8,
        tool_timeout_seconds: Optional[float] = 120.0,
        tool_result_max_chars: int = DEFAULT_SPILL_MAX_CHARS,
        tool_result_details_max_chars: int = DEFAULT_DETAILS_MAX_CHARS,
        tool_spill_dir: Any = None,
        tool_spill_ttl_seconds: Optional[float] = DEFAULT_SPILL_TTL_SECONDS,
        tool_execution: ToolExecutionMode = "parallel",
        context_transform: Optional[ContextTransform] = None,
        before_tool_call: Optional[BeforeToolCallHook] = None,
        after_tool_call: Optional[AfterToolCallHook] = None,
        should_stop_after_turn: Optional[ShouldStopAfterTurn] = None,
        pipeline: Optional["ToolExecutionPipeline"] = None,
    ) -> None:
        if isinstance(max_turns, bool) or not isinstance(max_turns, int):
            raise TypeError("max_turns must be an integer")
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        if isinstance(max_tool_calls_per_turn, bool) or not isinstance(
            max_tool_calls_per_turn, int
        ):
            raise TypeError("max_tool_calls_per_turn must be an integer")
        if max_tool_calls_per_turn < 1:
            raise ValueError("max_tool_calls_per_turn must be at least 1")
        if max_tool_calls_total is None:
            max_tool_calls_total = max_turns * max_tool_calls_per_turn
        elif isinstance(max_tool_calls_total, bool) or not isinstance(
            max_tool_calls_total,
            int,
        ):
            raise TypeError("max_tool_calls_total must be an integer or None")
        if max_tool_calls_total < 1:
            raise ValueError("max_tool_calls_total must be at least 1")
        if context_transform is not None and not callable(context_transform):
            raise TypeError("context_transform must be callable")
        if should_stop_after_turn is not None and not callable(should_stop_after_turn):
            raise TypeError("should_stop_after_turn must be callable")
        if pipeline is not None and not isinstance(pipeline, ToolExecutionPipeline):
            raise TypeError("pipeline must be a ToolExecutionPipeline")

        self.registry = registry if registry is not None else ToolRegistry()
        self.max_turns = max_turns
        self.max_tool_calls_per_turn = max_tool_calls_per_turn
        self.max_tool_calls_total = max_tool_calls_total
        self.context_transform = context_transform
        self.should_stop_after_turn = should_stop_after_turn
        # An optional pipeline composes with legacy single hooks: pipeline
        # policies run first, the legacy hook last (pre) or last (post).
        combined_before = (
            pipeline.compose_pre(before_tool_call)
            if pipeline is not None
            else before_tool_call
        )
        combined_after = (
            pipeline.compose_post(after_tool_call)
            if pipeline is not None
            else after_tool_call
        )
        self.tool_executor = ToolExecutor(
            self.registry,
            execution_mode=tool_execution,
            before_tool_call=combined_before,
            after_tool_call=combined_after,
            max_workers=max_tool_workers,
            tool_timeout_seconds=tool_timeout_seconds,
            result_max_chars=tool_result_max_chars,
            result_details_max_chars=tool_result_details_max_chars,
            spill_dir=tool_spill_dir,
            spill_ttl_seconds=tool_spill_ttl_seconds,
        )
        self._lock = RLock()
        self._active_abort: Optional[Event] = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._active_abort is not None

    def abort(self) -> bool:
        with self._lock:
            if self._active_abort is None:
                return False
            self._active_abort.set()
            return True

    def run(
        self,
        prompt: str,
        provider: TurnProvider,
        *,
        history: Sequence[AgentMessage] = (),
        context: Optional[Mapping[str, Any]] = None,
        run_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> Iterator[AgentEvent]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not callable(provider):
            raise TypeError("provider must be callable")
        if any(not isinstance(message, AgentMessage) for message in history):
            raise TypeError("history must contain AgentMessage values")

        resolved_run_id = self._identifier(run_id)
        first_turn_id = self._identifier(turn_id)
        context_snapshot = MappingProxyType(dict(context or {}))

        def event_stream() -> Generator[
            AgentEvent,
            None,
            tuple[AgentMessage, ...],
        ]:
            abort_event = self._start()
            # Freeze tool definitions for the whole run. Hot reloads apply to the
            # next run and cannot swap an implementation after the model has seen
            # a different schema or policy snapshot.
            run_registry = ToolRegistry(list(self.registry.snapshot()))
            run_tool_executor = ToolExecutor(
                run_registry,
                execution_mode=self.tool_executor.execution_mode,
                before_tool_call=self.tool_executor.before_tool_call,
                after_tool_call=self.tool_executor.after_tool_call,
                max_workers=self.tool_executor.max_workers,
                tool_timeout_seconds=self.tool_executor.tool_timeout_seconds,
                result_max_chars=self.tool_executor.result_max_chars,
                result_details_max_chars=(
                    self.tool_executor.result_details_max_chars
                ),
                spill_dir=self.tool_executor.spill_dir,
                spill_ttl_seconds=self.tool_executor.spill_ttl_seconds,
            )
            messages = list(history)
            new_messages: list[AgentMessage] = []
            user_message = AgentMessage(role="user", content=prompt.strip())
            messages.append(user_message)
            new_messages.append(user_message)
            current_turn_id = first_turn_id
            emitted_agent_end = False
            tool_calls_used = 0
            try:
                yield AgentEvent(
                    AgentEventType.AGENT_START,
                    resolved_run_id,
                    first_turn_id,
                )
                for turn_index in range(self.max_turns):
                    if abort_event.is_set():
                        raise AgentLoopAbortedError("agent loop aborted")
                    current_turn_id = (
                        first_turn_id
                        if turn_index == 0
                        else f"{first_turn_id}:{turn_index + 1}"
                    )
                    yield AgentEvent(
                        AgentEventType.TURN_START,
                        resolved_run_id,
                        current_turn_id,
                        metadata={"turn_index": turn_index},
                    )
                    if turn_index == 0:
                        yield AgentEvent(
                            AgentEventType.MESSAGE_START,
                            resolved_run_id,
                            current_turn_id,
                            message=user_message,
                        )
                        yield AgentEvent(
                            AgentEventType.MESSAGE_END,
                            resolved_run_id,
                            current_turn_id,
                            message=user_message,
                        )

                    provider_messages = self._transform_messages(
                        tuple(messages),
                        context_snapshot,
                    )
                    if abort_event.is_set():
                        raise AgentLoopAbortedError("agent loop aborted")
                    turn = provider(
                        provider_messages,
                        run_registry.specifications(),
                        context_snapshot,
                    )
                    if abort_event.is_set():
                        raise AgentLoopAbortedError("agent loop aborted")
                    if inspect.isawaitable(turn):
                        raise TypeError(
                            "synchronous turn provider returned an awaitable"
                        )
                    if not isinstance(turn, AssistantTurn):
                        raise TypeError("provider must return AssistantTurn")
                    if len(turn.tool_calls) > self.max_tool_calls_per_turn:
                        raise ValueError(
                            "assistant requested too many tool calls in one turn"
                        )
                    if (
                        tool_calls_used + len(turn.tool_calls)
                        > self.max_tool_calls_total
                    ):
                        raise AgentLoopLimitError(
                            "agent loop exceeded the cumulative tool call budget "
                            f"of {self.max_tool_calls_total}"
                        )
                    tool_calls_used += len(turn.tool_calls)

                    assistant_message = AgentMessage(
                        role="assistant",
                        content=turn.content,
                        metadata={
                            **dict(turn.metadata),
                            "tool_calls": [call.to_dict() for call in turn.tool_calls],
                        },
                    )
                    messages.append(assistant_message)
                    new_messages.append(assistant_message)
                    yield AgentEvent(
                        AgentEventType.MESSAGE_START,
                        resolved_run_id,
                        current_turn_id,
                        message=assistant_message,
                    )
                    if turn.content:
                        yield AgentEvent(
                            AgentEventType.MESSAGE_UPDATE,
                            resolved_run_id,
                            current_turn_id,
                            message=assistant_message,
                            delta=turn.content,
                        )
                    yield AgentEvent(
                        AgentEventType.MESSAGE_END,
                        resolved_run_id,
                        current_turn_id,
                        message=assistant_message,
                    )
                    if abort_event.is_set():
                        raise AgentLoopAbortedError("agent loop aborted")

                    batch = ToolBatchResult()
                    if turn.tool_calls:
                        batch = yield from run_tool_executor.execute_batch(
                            turn.tool_calls,
                            run_id=resolved_run_id,
                            turn_id=current_turn_id,
                            context=context_snapshot,
                            abort_event=abort_event,
                            allow_side_effects=turn_index < self.max_turns - 1,
                        )
                        for record in batch.records:
                            messages.append(record.message)
                            new_messages.append(record.message)
                        if abort_event.is_set():
                            raise AgentLoopAbortedError("agent loop aborted")

                    yield AgentEvent(
                        AgentEventType.TURN_END,
                        resolved_run_id,
                        current_turn_id,
                        message=assistant_message,
                        metadata={
                            "turn_index": turn_index,
                            "tool_batch": batch.to_dict(),
                        },
                    )
                    if abort_event.is_set():
                        raise AgentLoopAbortedError("agent loop aborted")
                    should_stop = (
                        self.should_stop_after_turn(turn, batch, tuple(messages))
                        if self.should_stop_after_turn is not None
                        else False
                    )
                    if abort_event.is_set():
                        raise AgentLoopAbortedError("agent loop aborted")
                    if not isinstance(should_stop, bool):
                        raise TypeError("should_stop_after_turn must return a boolean")
                    if should_stop or not turn.tool_calls or batch.terminate:
                        yield AgentEvent(
                            AgentEventType.AGENT_END,
                            resolved_run_id,
                            current_turn_id,
                            message=assistant_message,
                            metadata={
                                "messages": [
                                    message.to_dict() for message in new_messages
                                ]
                            },
                        )
                        emitted_agent_end = True
                        return tuple(new_messages)

                raise AgentLoopLimitError(f"agent loop exceeded {self.max_turns} turns")
            except GeneratorExit:
                raise
            except Exception as error:
                error_text = str(error) or error.__class__.__name__
                yield AgentEvent(
                    AgentEventType.RUNTIME_ERROR,
                    resolved_run_id,
                    current_turn_id,
                    error=error_text,
                    is_error=True,
                    metadata={"error_type": error.__class__.__name__},
                )
                if not emitted_agent_end:
                    yield AgentEvent(
                        AgentEventType.AGENT_END,
                        resolved_run_id,
                        current_turn_id,
                        error=error_text,
                        is_error=True,
                        metadata={
                            "messages": [message.to_dict() for message in new_messages]
                        },
                    )
                raise
            finally:
                self._finish(abort_event)

        return event_stream()

    def _transform_messages(
        self,
        messages: tuple[AgentMessage, ...],
        context: Mapping[str, Any],
    ) -> tuple[AgentMessage, ...]:
        if self.context_transform is None:
            return messages
        transformed = self.context_transform(messages, context)
        if isinstance(transformed, (str, bytes)):
            raise TypeError("context_transform must return agent messages")
        result = tuple(transformed)
        if any(not isinstance(message, AgentMessage) for message in result):
            raise TypeError("context_transform must return agent messages")
        return result

    def _start(self) -> Event:
        with self._lock:
            if self._active_abort is not None:
                raise AgentLoopBusyError("an agent loop run is already active")
            abort_event = Event()
            self._active_abort = abort_event
            return abort_event

    def _finish(self, abort_event: Event) -> None:
        with self._lock:
            if self._active_abort is abort_event:
                self._active_abort = None

    @staticmethod
    def _identifier(value: Optional[str]) -> str:
        if value is None:
            return uuid4().hex
        if not isinstance(value, str) or not value.strip():
            raise ValueError("runtime identifiers must be non-empty strings")
        return value.strip()
