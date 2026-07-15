"""Typed runtime messages and lifecycle events.

The event names intentionally follow Pi's agent-core protocol so UI, logging,
and persistence adapters can observe one stable stream without depending on
the concrete LLM or business-skill implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from time import time
from types import MappingProxyType
from typing import Any, Mapping, Optional
from uuid import uuid4


def _frozen_mapping(value: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


class AgentEventType(str, Enum):
    """Stable lifecycle event names exposed by the runtime."""

    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    MESSAGE_START = "message_start"
    MESSAGE_UPDATE = "message_update"
    MESSAGE_END = "message_end"
    TOOL_EXECUTION_START = "tool_execution_start"
    TOOL_EXECUTION_UPDATE = "tool_execution_update"
    TOOL_EXECUTION_END = "tool_execution_end"
    RUNTIME_ERROR = "runtime_error"


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """A runtime message independent from any provider payload shape."""

    role: str
    content: str
    id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: float = field(default_factory=time)
    status: str = "complete"
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("message role must be a non-empty string")
        if not isinstance(self.content, str):
            raise TypeError("message content must be a string")
        if self.status not in {"pending", "complete", "error"}:
            raise ValueError("message status must be pending, complete, or error")
        object.__setattr__(self, "role", self.role.strip())
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))

    def with_content(
        self, content: str, *, status: Optional[str] = None
    ) -> "AgentMessage":
        """Return a new message snapshot for streaming updates."""
        return replace(self, content=content, status=status or self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """One immutable observation from an agent run."""

    type: AgentEventType
    run_id: str
    turn_id: str
    timestamp: float = field(default_factory=time)
    message: Optional[AgentMessage] = None
    delta: str = ""
    error: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Mapping[str, Any] = field(default_factory=dict, compare=False)
    tool_result: Mapping[str, Any] = field(default_factory=dict, compare=False)
    is_error: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.type, AgentEventType):
            object.__setattr__(self, "type", AgentEventType(self.type))
        for field_name, value in (("run_id", self.run_id), ("turn_id", self.turn_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        if not isinstance(self.delta, str):
            raise TypeError("event delta must be a string")
        if not isinstance(self.is_error, bool):
            raise TypeError("event is_error must be a boolean")
        object.__setattr__(self, "tool_arguments", _frozen_mapping(self.tool_arguments))
        object.__setattr__(self, "tool_result", _frozen_mapping(self.tool_result))
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "timestamp": self.timestamp,
            "message": self.message.to_dict() if self.message else None,
            "delta": self.delta,
            "error": self.error,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "tool_arguments": dict(self.tool_arguments),
            "tool_result": dict(self.tool_result),
            "is_error": self.is_error,
            "metadata": dict(self.metadata),
        }
