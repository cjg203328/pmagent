"""Low-level multi-turn agent loop with structured tool execution.

This module mirrors Pi's separation between the small provider-neutral loop and
the higher-level application harness. It does not know about Streamlit, model
vendors, databases, or ArtPM business rules.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import inspect
from queue import Queue
from threading import Event, RLock
from types import MappingProxyType
from typing import Any, Optional
from uuid import uuid4

from .events import AgentEvent, AgentEventType, AgentMessage
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
        return bool(self.records) and all(
            record.result.terminate for record in self.records
        )

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
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        if execution_mode not in {"parallel", "sequential"}:
            raise ValueError("tool execution mode is invalid")
        if before_tool_call is not None and not callable(before_tool_call):
            raise TypeError("before_tool_call must be callable")
        if after_tool_call is not None and not callable(after_tool_call):
            raise TypeError("after_tool_call must be callable")
        self.registry = registry
        self.execution_mode = execution_mode
        self.before_tool_call = before_tool_call
        self.after_tool_call = after_tool_call

    def execute_batch(
        self,
        calls: Sequence[ToolCall],
        *,
        run_id: str,
        turn_id: str,
        context: Mapping[str, Any],
        abort_event: Event,
    ) -> Iterator[AgentEvent]:
        prepared: list[_PreparedToolCall] = []
        finalized: list[Optional[ToolResult]] = [None] * len(calls)

        for index, call in enumerate(calls):
            yield self._event(
                AgentEventType.TOOL_EXECUTION_START,
                call,
                run_id,
                turn_id,
            )
            item = self._prepare(index, call, context, abort_event)
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
        worker_count = 1 if force_sequential else max(1, len(executable))
        if executable:
            updates: Queue[tuple[str, int, ToolResult]] = Queue()
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="artpm-tool",
            ) as pool:
                if force_sequential:
                    for item in executable:
                        pool.submit(
                            self._execute_worker,
                            item,
                            context,
                            abort_event,
                            updates,
                        )
                        yield from self._drain_one(
                            item,
                            updates,
                            finalized,
                            run_id,
                            turn_id,
                        )
                else:
                    for item in executable:
                        pool.submit(
                            self._execute_worker,
                            item,
                            context,
                            abort_event,
                            updates,
                        )
                    completed = 0
                    while completed < len(executable):
                        kind, index, result = updates.get()
                        item = prepared[index]
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
                        completed += 1
                        yield self._event(
                            AgentEventType.TOOL_EXECUTION_END,
                            item.call,
                            run_id,
                            turn_id,
                            result=result,
                        )

        records: list[ToolExecutionRecord] = []
        for index, call in enumerate(calls):
            result = finalized[index]
            if result is None:
                raise RuntimeError(f"tool call did not finalize: {call.name}")
            message = self._result_message(call, result)
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
            records.append(ToolExecutionRecord(call, result, message))
        return ToolBatchResult(tuple(records))

    def _prepare(
        self,
        index: int,
        call: ToolCall,
        context: Mapping[str, Any],
        abort_event: Event,
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
        updates: Queue[tuple[str, int, ToolResult]],
    ) -> None:
        tool = prepared.tool
        if tool is None:
            updates.put(("done", prepared.index, ToolResult.error("Tool not found")))
            return

        accepting_updates = Event()
        accepting_updates.set()

        def on_update(partial: ToolResult) -> None:
            if accepting_updates.is_set():
                updates.put(("update", prepared.index, normalize_tool_result(partial)))

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
            accepting_updates.clear()

        if self.after_tool_call is not None:
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
        updates.put(("done", prepared.index, result))

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
        tool_execution: ToolExecutionMode = "parallel",
        context_transform: Optional[ContextTransform] = None,
        before_tool_call: Optional[BeforeToolCallHook] = None,
        after_tool_call: Optional[AfterToolCallHook] = None,
        should_stop_after_turn: Optional[ShouldStopAfterTurn] = None,
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
        if context_transform is not None and not callable(context_transform):
            raise TypeError("context_transform must be callable")
        if should_stop_after_turn is not None and not callable(should_stop_after_turn):
            raise TypeError("should_stop_after_turn must be callable")

        self.registry = registry if registry is not None else ToolRegistry()
        self.max_turns = max_turns
        self.max_tool_calls_per_turn = max_tool_calls_per_turn
        self.context_transform = context_transform
        self.should_stop_after_turn = should_stop_after_turn
        self.tool_executor = ToolExecutor(
            self.registry,
            execution_mode=tool_execution,
            before_tool_call=before_tool_call,
            after_tool_call=after_tool_call,
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

        def event_stream() -> Iterator[AgentEvent]:
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
            )
            messages = list(history)
            new_messages: list[AgentMessage] = []
            user_message = AgentMessage(role="user", content=prompt.strip())
            messages.append(user_message)
            new_messages.append(user_message)
            current_turn_id = first_turn_id
            emitted_agent_end = False
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
                    turn = provider(
                        provider_messages,
                        run_registry.specifications(),
                        context_snapshot,
                    )
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

                    batch = ToolBatchResult()
                    if turn.tool_calls:
                        batch = yield from run_tool_executor.execute_batch(
                            turn.tool_calls,
                            run_id=resolved_run_id,
                            turn_id=current_turn_id,
                            context=context_snapshot,
                            abort_event=abort_event,
                        )
                        for record in batch.records:
                            messages.append(record.message)
                            new_messages.append(record.message)

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
                    should_stop = (
                        self.should_stop_after_turn(turn, batch, tuple(messages))
                        if self.should_stop_after_turn is not None
                        else False
                    )
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
