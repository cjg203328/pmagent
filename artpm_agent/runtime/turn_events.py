"""Best-effort lifecycle recording for the canonical turn service."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from .events import AgentEvent, AgentEventType, EventDomain

logger = logging.getLogger(__name__)


class TurnEventRecorder:
    """Publish one lifecycle stream without coupling the turn to a sink.

    The event bus is the live delivery path when supplied, while the
    ``SessionStore`` remains the durable source of truth.  Hosts commonly
    provide both services, so they must not be treated as mutually exclusive.
    Recording failures are diagnostics, not a reason to fail the user request.
    """

    def __init__(self, *, event_bus: Any = None, session_store: Any = None) -> None:
        self.event_bus = event_bus
        self.session_store = session_store

    def emit(
        self,
        ctx: Any,
        event_type: AgentEventType,
        *,
        error: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        tool_name: Optional[str] = None,
        tool_arguments: Optional[Mapping[str, Any]] = None,
        tool_result: Optional[Mapping[str, Any]] = None,
    ) -> AgentEvent:
        extra = getattr(ctx, "extra", None)
        extra = extra if isinstance(extra, Mapping) else {}
        turn_id = str(getattr(ctx, "turn_id", "")).strip() or "unknown-turn"
        run_id = str(extra.get("run_id") or "").strip() or turn_id
        scope = getattr(ctx, "scope", None)
        scope_metadata = {
            "tenant_id": str(
                getattr(scope, "tenant_id", "") or extra.get("tenant_id") or "local"
            ),
            "workspace_id": str(
                getattr(scope, "workspace_id", "")
                or extra.get("workspace_id")
                or "local-default"
            ),
            "actor_id": str(
                getattr(scope, "actor_id", "")
                or extra.get("actor_id")
                or extra.get("principal_id")
                or "local-user"
            ),
            "actor_role": str(
                getattr(scope, "actor_role", "")
                or extra.get("actor_role")
                or "user"
            ),
        }
        event_metadata = dict(metadata or {})
        event_metadata.update(scope_metadata)
        event = AgentEvent(
            type=event_type,
            run_id=run_id or turn_id,
            turn_id=turn_id or run_id,
            domain=EventDomain.SESSION,
            error=error,
            is_error=error is not None,
            tool_name=tool_name,
            tool_arguments=dict(tool_arguments or {}),
            tool_result=dict(tool_result or {}),
            metadata=event_metadata,
        )
        if self.event_bus is not None:
            try:
                self.event_bus.publish(event)
            except Exception:  # noqa: BLE001 - observability must not block turns
                logger.warning("turn lifecycle event bus delivery failed", exc_info=True)

        # Keep durable replay independent from the optional live bus.  A bus
        # may have no sink (as is the case for the API/UI hosts), and publishing
        # alone must never silently discard the audit trail.  The two sinks use
        # separate guards so a transient live-bus failure cannot suppress the
        # durable append.
        if self.session_store is not None:
            try:
                conversation_id = str(
                    getattr(ctx, "conversation_id", "")
                    or extra.get("conversation_id")
                    or ""
                ).strip()
                if conversation_id:
                    scope = getattr(ctx, "scope", None)
                    workspace_id = str(
                        getattr(scope, "workspace_id", "")
                        or extra.get("workspace_id")
                        or "local-default"
                    )
                    self.session_store.append_event(
                        conversation_id,
                        event,
                        workspace_id=workspace_id,
                    )
            except Exception:  # noqa: BLE001 - observability must not block turns
                logger.warning("turn lifecycle event persistence failed", exc_info=True)
        return event


def recorder_for_turn(ctx: Any) -> TurnEventRecorder:
    """Return the one lifecycle recorder owned by a turn context."""

    existing = getattr(ctx, "_turn_event_recorder", None)
    if isinstance(existing, TurnEventRecorder):
        return existing
    services = getattr(ctx, "services", None)
    recorder = TurnEventRecorder(
        event_bus=getattr(services, "event_bus", None),
        session_store=getattr(services, "session_store", None),
    )
    try:
        ctx._turn_event_recorder = recorder
    except Exception:  # noqa: BLE001 - compatibility contexts may be immutable
        pass
    return recorder


__all__ = ["TurnEventRecorder", "recorder_for_turn"]
