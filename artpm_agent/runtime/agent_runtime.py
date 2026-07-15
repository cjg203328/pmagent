"""Provider-neutral synchronous agent runtime.

This module owns lifecycle state and event ordering. Domain routing, model
selection, and persistence remain adapters around the runtime instead of being
embedded in the UI.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
import logging
from threading import Event, RLock
from typing import Any, Optional
from uuid import uuid4

from .events import AgentEvent, AgentEventType, AgentMessage


logger = logging.getLogger(__name__)

ResponseStream = Callable[[str, Mapping[str, Any]], Iterable[str] | str]
ContextTransform = Callable[[Mapping[str, Any]], Mapping[str, Any]]
EventSubscriber = Callable[[AgentEvent], None]


class AgentRunBusyError(RuntimeError):
    """Raised when a second run starts before the current run settles."""


class AgentRunAbortedError(RuntimeError):
    """Raised after an abort request is observed between response chunks."""


class AgentSubscriberError(RuntimeError):
    """Raised when a critical lifecycle subscriber cannot persist an event."""


@dataclass(frozen=True, slots=True)
class AgentState:
    """Read-only snapshot of runtime state."""

    messages: tuple[AgentMessage, ...]
    is_running: bool
    streaming_message: Optional[AgentMessage]
    session_id: Optional[str]
    active_run_id: Optional[str]
    active_turn_id: Optional[str]
    error: Optional[str]


class AgentRuntime:
    """Run one response stream and expose a Pi-style lifecycle protocol."""

    def __init__(
        self,
        *,
        max_messages: int = 200,
        context_transform: Optional[ContextTransform] = None,
    ) -> None:
        if isinstance(max_messages, bool) or not isinstance(max_messages, int):
            raise TypeError("max_messages must be an integer")
        if max_messages < 2:
            raise ValueError("max_messages must be at least 2")
        if context_transform is not None and not callable(context_transform):
            raise TypeError("context_transform must be callable")

        self._messages: deque[AgentMessage] = deque(maxlen=max_messages)
        self._context_transform = context_transform
        self._subscribers: list[tuple[EventSubscriber, bool]] = []
        self._lock = RLock()
        self._abort_requested = Event()
        self._is_running = False
        self._streaming_message: Optional[AgentMessage] = None
        self._session_id: Optional[str] = None
        self._active_run_id: Optional[str] = None
        self._active_turn_id: Optional[str] = None
        self._error: Optional[str] = None

    @property
    def state(self) -> AgentState:
        with self._lock:
            return AgentState(
                messages=tuple(self._messages),
                is_running=self._is_running,
                streaming_message=self._streaming_message,
                session_id=self._session_id,
                active_run_id=self._active_run_id,
                active_turn_id=self._active_turn_id,
                error=self._error,
            )

    def subscribe(
        self,
        subscriber: EventSubscriber,
        *,
        critical: bool = True,
    ) -> Callable[[], None]:
        """Register a listener and return an unsubscribe callback.

        Critical subscribers are barriers for persistence and audit adapters:
        their failures fail the run. Set ``critical=False`` for best-effort
        telemetry that should only be logged.
        """
        if not callable(subscriber):
            raise TypeError("subscriber must be callable")
        if not isinstance(critical, bool):
            raise TypeError("critical must be a boolean")
        subscription = (subscriber, critical)
        with self._lock:
            self._subscribers.append(subscription)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(subscription)
                except ValueError:
                    pass

        return unsubscribe

    def abort(self) -> bool:
        """Request cooperative cancellation of the active response stream."""
        with self._lock:
            if not self._is_running:
                return False
            self._abort_requested.set()
            return True

    def reset(self) -> None:
        """Clear in-memory runtime state when no run is active."""
        with self._lock:
            if self._is_running:
                raise AgentRunBusyError("cannot reset while an agent run is active")
            self._messages.clear()
            self._streaming_message = None
            self._session_id = None
            self._error = None

    def run(
        self,
        prompt: str,
        responder: ResponseStream,
        *,
        context: Optional[Mapping[str, Any]] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Iterator[AgentEvent]:
        """Execute one turn and yield lifecycle events in deterministic order."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not callable(responder):
            raise TypeError("responder must be callable")

        resolved_run_id = self._identifier(run_id, "run_id")
        resolved_turn_id = self._identifier(turn_id, "turn_id")
        resolved_session_id = self._optional_identifier(session_id, "session_id")
        context_snapshot = dict(context or {})
        metadata_snapshot = dict(metadata or {})

        def event_stream() -> Iterator[AgentEvent]:
            self._start(resolved_run_id, resolved_turn_id, resolved_session_id)
            user_message = AgentMessage(
                role="user",
                content=prompt.strip(),
                metadata=metadata_snapshot,
            )
            assistant_message = AgentMessage(
                role="assistant",
                content="",
                status="pending",
                metadata=metadata_snapshot,
            )

            try:
                yield self._publish(
                    self._event(
                        AgentEventType.AGENT_START,
                        resolved_run_id,
                        resolved_turn_id,
                        metadata=metadata_snapshot,
                    )
                )
                yield self._publish(
                    self._event(
                        AgentEventType.TURN_START,
                        resolved_run_id,
                        resolved_turn_id,
                        metadata=metadata_snapshot,
                    )
                )
                for event_type in (
                    AgentEventType.MESSAGE_START,
                    AgentEventType.MESSAGE_END,
                ):
                    published = self._publish(
                        self._event(
                            event_type,
                            resolved_run_id,
                            resolved_turn_id,
                            message=user_message,
                            metadata=metadata_snapshot,
                        )
                    )
                    if event_type == AgentEventType.MESSAGE_END:
                        self._store_message(user_message)
                    yield published

                self._set_streaming_message(assistant_message)
                yield self._publish(
                    self._event(
                        AgentEventType.MESSAGE_START,
                        resolved_run_id,
                        resolved_turn_id,
                        message=assistant_message,
                        metadata=metadata_snapshot,
                    )
                )

                transformed_context = self._transform_context(context_snapshot)
                chunks: list[str] = []
                response = responder(prompt, transformed_context)
                response_chunks: Iterable[str] = (
                    (response,) if isinstance(response, str) else response
                )
                for chunk in response_chunks:
                    if self._abort_requested.is_set():
                        raise AgentRunAbortedError("agent run aborted")
                    if chunk is None:
                        continue
                    if not isinstance(chunk, str):
                        raise TypeError("responder chunks must be strings")
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    assistant_message = assistant_message.with_content("".join(chunks))
                    self._set_streaming_message(assistant_message)
                    yield self._publish(
                        self._event(
                            AgentEventType.MESSAGE_UPDATE,
                            resolved_run_id,
                            resolved_turn_id,
                            message=assistant_message,
                            delta=chunk,
                            metadata=metadata_snapshot,
                        )
                    )

                if self._abort_requested.is_set():
                    raise AgentRunAbortedError("agent run aborted")
                response_text = "".join(chunks)
                if not response_text.strip():
                    raise ValueError("agent responder returned no text")

                assistant_message = assistant_message.with_content(
                    response_text,
                    status="complete",
                )
                self._set_streaming_message(None)
                published = self._publish(
                    self._event(
                        AgentEventType.MESSAGE_END,
                        resolved_run_id,
                        resolved_turn_id,
                        message=assistant_message,
                        metadata=metadata_snapshot,
                    )
                )
                self._store_message(assistant_message)
                yield published
                yield self._publish(
                    self._event(
                        AgentEventType.TURN_END,
                        resolved_run_id,
                        resolved_turn_id,
                        message=assistant_message,
                        metadata=metadata_snapshot,
                    )
                )
                yield self._publish(
                    self._event(
                        AgentEventType.AGENT_END,
                        resolved_run_id,
                        resolved_turn_id,
                        message=assistant_message,
                        metadata=metadata_snapshot,
                    )
                )
            except GeneratorExit:
                raise
            except Exception as error:
                error_text = str(error) or error.__class__.__name__
                error_message = AgentMessage(
                    role="assistant",
                    content=assistant_message.content or error_text,
                    id=assistant_message.id,
                    timestamp=assistant_message.timestamp,
                    status="error",
                    metadata={
                        **metadata_snapshot,
                        "error": error_text,
                        "error_type": error.__class__.__name__,
                    },
                )
                self._set_error(error_text)
                self._set_streaming_message(None)
                yield self._publish(
                    self._event(
                        AgentEventType.RUNTIME_ERROR,
                        resolved_run_id,
                        resolved_turn_id,
                        message=error_message,
                        error=error_text,
                        metadata=metadata_snapshot,
                    ),
                    suppress_critical=True,
                )
                published = self._publish(
                    self._event(
                        AgentEventType.MESSAGE_END,
                        resolved_run_id,
                        resolved_turn_id,
                        message=error_message,
                        error=error_text,
                        metadata=metadata_snapshot,
                    ),
                    suppress_critical=True,
                )
                self._store_message(error_message)
                yield published
                yield self._publish(
                    self._event(
                        AgentEventType.TURN_END,
                        resolved_run_id,
                        resolved_turn_id,
                        message=error_message,
                        error=error_text,
                        metadata=metadata_snapshot,
                    ),
                    suppress_critical=True,
                )
                yield self._publish(
                    self._event(
                        AgentEventType.AGENT_END,
                        resolved_run_id,
                        resolved_turn_id,
                        message=error_message,
                        error=error_text,
                        metadata=metadata_snapshot,
                    ),
                    suppress_critical=True,
                )
                raise
            finally:
                self._finish()

        return event_stream()

    @staticmethod
    def _identifier(value: Optional[str], field_name: str) -> str:
        if value is None:
            return uuid4().hex
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _optional_identifier(value: Optional[str], field_name: str) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        return value.strip()

    def _transform_context(self, context: Mapping[str, Any]) -> dict[str, Any]:
        if self._context_transform is None:
            return dict(context)
        transformed = self._context_transform(dict(context))
        if not isinstance(transformed, Mapping):
            raise TypeError("context_transform must return a mapping")
        return dict(transformed)

    def _start(
        self,
        run_id: str,
        turn_id: str,
        session_id: Optional[str],
    ) -> None:
        with self._lock:
            if self._is_running:
                raise AgentRunBusyError("an agent run is already active")
            if self._session_id != session_id:
                self._messages.clear()
                self._session_id = session_id
            self._is_running = True
            self._active_run_id = run_id
            self._active_turn_id = turn_id
            self._streaming_message = None
            self._error = None
            self._abort_requested.clear()

    def _finish(self) -> None:
        with self._lock:
            self._is_running = False
            self._active_run_id = None
            self._active_turn_id = None
            self._streaming_message = None
            self._abort_requested.clear()

    def _store_message(self, message: AgentMessage) -> None:
        with self._lock:
            for index, existing in enumerate(self._messages):
                if existing.id == message.id:
                    self._messages[index] = message
                    return
            self._messages.append(message)

    def _set_streaming_message(self, message: Optional[AgentMessage]) -> None:
        with self._lock:
            self._streaming_message = message

    def _set_error(self, error: str) -> None:
        with self._lock:
            self._error = error

    def _publish(
        self,
        event: AgentEvent,
        *,
        suppress_critical: bool = False,
    ) -> AgentEvent:
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber, critical in subscribers:
            try:
                subscriber(event)
            except Exception as error:
                if critical and not suppress_critical:
                    raise AgentSubscriberError(
                        f"critical subscriber failed for {event.type.value}"
                    ) from error
                logger.exception(
                    "Agent runtime subscriber failed for %s", event.type.value
                )
        return event

    @staticmethod
    def _event(
        event_type: AgentEventType,
        run_id: str,
        turn_id: str,
        *,
        message: Optional[AgentMessage] = None,
        delta: str = "",
        error: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> AgentEvent:
        return AgentEvent(
            type=event_type,
            run_id=run_id,
            turn_id=turn_id,
            message=message,
            delta=delta,
            error=error,
            metadata=metadata or {},
        )
