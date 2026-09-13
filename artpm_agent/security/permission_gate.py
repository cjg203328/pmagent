"""Host-side preflight for model-visible tools.

The model can request a tool call, but it cannot approve that call itself.  The
preflight creates a durable :class:`PermissionRequest` and returns a blocked
decision; the UI or another trusted host later consumes that request exactly
once.  Keeping this adapter independent from Streamlit makes the same policy
usable by API and LangGraph runners.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Optional

from artpm_agent.runtime.tools import AgentTool, BeforeToolCallDecision, ToolCall
from artpm_agent.security.access_mode import access_decision, effective_access_risk

logger = logging.getLogger(__name__)

_RISKS = frozenset({"low", "medium", "high", "critical", "untrusted"})


def _text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _server_policy_allows(action: str) -> bool:
    """Fail closed when the static capability policy denies an action."""

    try:
        from artpm_agent.workflows.risk_policy import action_is_allowed

        return bool(action_is_allowed(action))
    except Exception:  # noqa: BLE001 - an unavailable policy cannot grant access
        logger.exception("Unable to evaluate server risk policy for %s", action)
        return False


def permission_preflight(
    call: ToolCall,
    tool: AgentTool,
    arguments: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Optional[BeforeToolCallDecision]:
    """Create a pending request for a side-effecting model tool.

    ``None`` means the tool is read-only and may proceed.  A protected tool is
    always blocked in this pass, including when persistence fails; this keeps a
    database outage from turning into an implicit allow.
    """

    if not (tool.requires_approval or not tool.read_only):
        return None

    action = f"tool.{tool.name}"
    if not _server_policy_allows(action):
        return BeforeToolCallDecision(
            block=True,
            reason=f"Capability is blocked by the server risk policy: {tool.name}",
        )

    risk = effective_access_risk(
        _text(getattr(tool, "risk", None), "untrusted"),
        action=tool.name,
        arguments=arguments,
    )
    required_role = "admin" if risk in {"critical", "untrusted"} else "user"
    if access_decision(
        context,
        read_only=tool.read_only,
        requires_approval=tool.requires_approval,
        risk=risk,
        required_role=required_role,
        auto_approval_allowed=tool.auto_approval_allowed,
    ) == "allow":
        return BeforeToolCallDecision(
            approved=True,
            reason=f"conversation_full_access:{tool.name}",
        )

    store = context.get("permission_store") if isinstance(context, Mapping) else None
    if store is None or not callable(getattr(store, "create_request", None)):
        return BeforeToolCallDecision(
            block=True,
            reason=f"Tool requires explicit host approval: {tool.name}",
        )

    # Untrusted/critical integrations are administrator actions by default.
    conversation_id = _text(context.get("conversation_id"), "unknown-conversation")
    turn_id = _text(context.get("turn_id"), "unknown-turn")
    workspace_id = _text(context.get("workspace_id"), "local-default")
    tenant_id = _text(context.get("tenant_id"), "local")
    agent_id = _text(context.get("agent_id"), "artpm-agent")
    call_id = _text(getattr(call, "id", None), "unknown-call")

    # PermissionStore performs the strict JSON/depth/cycle checks and stores a
    # separately redacted preview.  Do not put host approval fields in the
    # payload; AgentLoop already strips them before this hook is called.
    payload = {
        "tool_name": tool.name,
        "arguments": dict(arguments),
    }
    resource = {
        "tool": tool.name,
        "label": getattr(tool, "label", tool.name),
        "call_id": call_id,
    }
    idempotency_key = f"tool:{conversation_id}:{turn_id}:{call_id}:{tool.name}"
    try:
        request = store.create_request(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            agent_id=agent_id,
            source="tool",
            action=action,
            resource=resource,
            risk=risk,
            required_role=required_role,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except Exception:
        logger.exception("Unable to persist permission request for tool %s", tool.name)
        return BeforeToolCallDecision(
            block=True,
            reason="Permission request could not be persisted; tool execution was blocked",
        )

    return BeforeToolCallDecision(
        block=True,
        reason=f"permission_request:{request.id}",
    )


__all__ = ["permission_preflight"]
