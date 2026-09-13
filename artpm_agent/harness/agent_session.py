"""Feature-gated agent session with append-only event persistence."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from typing import Any, Optional

from artpm_agent.memory.session_store import SessionStore
from artpm_agent.runtime.agent_loop import AgentLoop, TurnProvider
from artpm_agent.runtime.events import AgentEvent, AgentMessage


class ModelToolCallsDisabledError(RuntimeError):
    """Raised when the structured tool loop is not enabled by the host."""


class AgentSession:
    """Bind one provider-neutral loop to an append-only conversation log.

    Persistence is a delivery barrier: an event is appended successfully before
    it is yielded to UI or API consumers. The host can disable model-driven tool
    calls as an operational rollback without bypassing this boundary when active.
    """

    def __init__(
        self,
        loop: AgentLoop,
        provider: TurnProvider,
        store: SessionStore,
        *,
        model_tool_calls_enabled: bool = False,
    ) -> None:
        if not isinstance(loop, AgentLoop):
            raise TypeError("loop must be an AgentLoop")
        if not callable(provider):
            raise TypeError("provider must be callable")
        if not isinstance(store, SessionStore):
            raise TypeError("store must be a SessionStore")
        if not isinstance(model_tool_calls_enabled, bool):
            raise TypeError("model_tool_calls_enabled must be a boolean")
        self.loop = loop
        self.provider = provider
        self.store = store
        self.model_tool_calls_enabled = model_tool_calls_enabled

    def run(
        self,
        prompt: str,
        *,
        conversation_id: str,
        workspace_id: Optional[str] = None,
        history: Sequence[AgentMessage] = (),
        context: Optional[Mapping[str, Any]] = None,
        run_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> Iterator[AgentEvent]:
        """Run and durably append each event before exposing it downstream."""
        if not self.model_tool_calls_enabled:
            raise ModelToolCallsDisabledError(
                "model-driven tool calls are disabled by the host configuration"
            )
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")

        events = self.loop.run(
            prompt,
            self.provider,
            history=history,
            context=context,
            run_id=run_id,
            turn_id=turn_id,
        )
        event_bus = context.get("event_bus") if isinstance(context, Mapping) else None

        def persisted_events() -> Iterator[AgentEvent]:
            try:
                for event in events:
                    scope_metadata = {
                        key: str(context[key]).strip()
                        for key in (
                            "tenant_id",
                            "workspace_id",
                            "actor_id",
                        )
                        if isinstance(context, Mapping)
                        and str(context.get(key) or "").strip()
                    }
                    if scope_metadata:
                        event = replace(
                            event,
                            metadata={**dict(event.metadata), **scope_metadata},
                        )
                    self.store.append_event(
                        conversation_id.strip(),
                        event,
                        workspace_id=workspace_id,
                    )
                    if event_bus is not None:
                        try:
                            event_bus.publish(event)
                        except Exception:
                            pass
                    yield event
            finally:
                close = getattr(events, "close", None)
                if callable(close):
                    close()

        return persisted_events()
