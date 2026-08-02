from __future__ import annotations

from pathlib import Path

from artpm_agent.agent import ArtPMAgent
from artpm_agent.runtime import AssistantTurn
from artpm_agent.runtime.tools import AgentTool, ToolCall, ToolRegistry, ToolResult
from artpm_agent.security import PermissionStore, permission_preflight


def _tool(
    *,
    name: str = "publish_report",
    read_only: bool = False,
    risk: str = "medium",
    auto_approval_allowed: bool = False,
) -> AgentTool:
    return AgentTool(
        name=name,
        label="Publish report",
        description="Publish one project report",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "api_key": {"type": "string"},
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
        execute=lambda *_args: ToolResult("published"),
        requires_approval=not read_only,
        read_only=read_only,
        risk=risk,
        auto_approval_allowed=auto_approval_allowed,
    )


def test_model_tool_preflight_creates_redacted_persistent_request(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")
    call = ToolCall(
        name="publish_report",
        id="call-1",
        arguments={"project_id": 7, "api_key": "model-secret"},
    )
    tool = _tool()

    # AgentLoop validates/normalizes before calling the preflight. Use the
    # prepared subset here to mirror that boundary.
    decision = permission_preflight(
        call,
        tool,
        {"project_id": 7, "api_key": "model-secret"},
        {
            "permission_store": store,
            "workspace_id": "workspace-1",
            "conversation_id": "conversation-1",
            "turn_id": "turn-1",
            "agent_id": "planner",
        },
    )

    assert decision is not None and decision.block is True
    requests = store.list_pending()
    assert len(requests) == 1
    request = requests[0]
    assert request.source == "tool"
    assert request.action == "tool.publish_report"
    assert request.payload["arguments"]["api_key"] == "model-secret"
    assert request.redacted_arguments["arguments"]["api_key"] == "[REDACTED]"
    assert decision.reason == f"permission_request:{request.id}"


def test_untrusted_tool_requires_admin_and_read_only_tool_skips_gate(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")
    context = {
        "permission_store": store,
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
    }

    assert (
        permission_preflight(
            ToolCall(name="publish_report", id="read", arguments={}),
            _tool(read_only=True),
            {},
            context,
        )
        is None
    )
    decision = permission_preflight(
        ToolCall(name="publish_report", id="admin", arguments={}),
        _tool(risk="untrusted"),
        {},
        context,
    )

    assert decision is not None and decision.block is True
    assert store.list_pending()[0].required_role == "admin"


def test_permission_persistence_failure_blocks_tool_without_implicit_allow():
    class BrokenStore:
        @staticmethod
        def create_request(**_kwargs):
            raise OSError("database unavailable")

    decision = permission_preflight(
        ToolCall(name="publish_report", id="call-fail", arguments={}),
        _tool(),
        {},
        {
            "permission_store": BrokenStore(),
            "conversation_id": "conversation-1",
            "turn_id": "turn-1",
        },
    )

    assert decision is not None
    assert decision.block is True
    assert decision.approved is False
    assert "could not be persisted" in decision.reason


def test_static_denied_command_is_not_presented_as_approvable(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")
    tool = AgentTool(
        name="execute_command",
        description="Execute a system command",
        execute=lambda *_args: ToolResult("must not run"),
        requires_approval=True,
        read_only=False,
        risk="critical",
    )

    decision = permission_preflight(
        ToolCall(
            name="execute_command",
            id="command-call",
            arguments={"command": "whoami"},
        ),
        tool,
        {"command": "whoami"},
        {
            "permission_store": store,
            "workspace_id": "workspace-1",
            "conversation_id": "conversation-1",
            "turn_id": "turn-1",
        },
    )

    assert decision is not None and decision.block is True
    assert "blocked by the server risk policy" in decision.reason
    assert store.list_pending(workspace_id="workspace-1") == []


def test_full_access_preapproves_only_trusted_medium_tool(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")
    context = {
        "permission_mode": "full_access",
        "permission_store": store,
        "workspace_id": "workspace-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
    }

    decision = permission_preflight(
        ToolCall(name="record_progress", id="full", arguments={"project_id": 7}),
        _tool(name="record_progress", auto_approval_allowed=True),
        {"project_id": 7},
        context,
    )

    assert decision is not None
    assert decision.approved is True
    assert decision.block is False
    assert decision.reason == "conversation_full_access:record_progress"
    assert store.list_pending(workspace_id="workspace-1") == []


def test_full_access_does_not_bypass_high_risk_tool_confirmation(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")
    decision = permission_preflight(
        ToolCall(name="publish_report", id="high", arguments={"project_id": 7}),
        _tool(risk="high", auto_approval_allowed=True),
        {"project_id": 7},
        {
            "permission_mode": "full_access",
            "permission_store": store,
            "workspace_id": "workspace-1",
            "conversation_id": "conversation-1",
            "turn_id": "turn-1",
        },
    )

    assert decision is not None and decision.block is True
    assert decision.approved is False
    pending = store.list_pending(workspace_id="workspace-1")
    assert len(pending) == 1
    assert pending[0].risk == "high"


def test_full_access_does_not_trust_a_medium_label_on_external_action(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")
    decision = permission_preflight(
        ToolCall(name="publish_report", id="publish", arguments={"project_id": 7}),
        _tool(risk="medium", auto_approval_allowed=True),
        {"project_id": 7},
        {
            "permission_mode": "full_access",
            "permission_store": store,
            "workspace_id": "workspace-1",
            "conversation_id": "conversation-1",
            "turn_id": "turn-1",
        },
    )

    assert decision is not None and decision.block is True
    pending = store.list_pending(workspace_id="workspace-1")
    assert len(pending) == 1
    assert pending[0].risk == "high"


def test_agent_loop_executes_trusted_medium_tool_in_full_access(tmp_path: Path):
    calls: list[dict] = []

    def execute(_call_id, arguments, _abort_event, _on_update):
        calls.append(dict(arguments))
        return ToolResult("recorded")

    tool = AgentTool(
        name="record_progress",
        description="Record one progress update",
        execute=execute,
        requires_approval=True,
        read_only=False,
        risk="medium",
        auto_approval_allowed=True,
    )
    agent = object.__new__(ArtPMAgent)
    agent.tool_registry = ToolRegistry([tool])
    loop = agent.create_agent_loop()
    provider_calls = 0

    def provider(_messages, _tools, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return AssistantTurn(
                tool_calls=(
                    ToolCall(
                        name="record_progress",
                        id="tool-call-full",
                        arguments={"progress": 50},
                    ),
                )
            )
        return AssistantTurn("Recorded.")

    store = PermissionStore(tmp_path / "permissions.db")
    list(
        loop.run(
            "record it",
            provider,
            context={
                "permission_mode": "full_access",
                "permission_store": store,
                "workspace_id": "workspace-1",
                "conversation_id": "conversation-1",
                "turn_id": "turn-1",
            },
        )
    )

    assert calls == [{"progress": 50, "approved": True}]
    assert store.list_pending(workspace_id="workspace-1") == []


def test_agent_default_model_tool_loop_creates_request_without_execution(tmp_path: Path):
    calls: list[dict] = []

    def execute(_call_id, arguments, _abort_event, _on_update):
        calls.append(dict(arguments))
        return ToolResult("published")

    tool = AgentTool(
        name="publish_report",
        description="Publish one project report",
        execute=execute,
        requires_approval=True,
        read_only=False,
        risk="medium",
    )
    agent = object.__new__(ArtPMAgent)
    agent.tool_registry = ToolRegistry([tool])
    loop = agent.create_agent_loop()
    provider_calls = 0

    def provider(messages, _tools, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return AssistantTurn(
                tool_calls=(
                    ToolCall(
                        name="publish_report",
                        id="tool-call-1",
                        arguments={"project_id": 7},
                    ),
                )
            )
        assert messages[-1].role == "toolResult"
        assert messages[-1].status == "error"
        assert "permission_request:" in messages[-1].content
        return AssistantTurn("Waiting for confirmation.")

    store = PermissionStore(tmp_path / "permissions.db")
    list(
        loop.run(
            "publish it",
            provider,
            context={
                "permission_store": store,
                "workspace_id": "workspace-1",
                "conversation_id": "conversation-1",
                "turn_id": "turn-1",
                "agent_id": "planner",
            },
        )
    )

    assert calls == []
    requests = store.list_pending()
    assert len(requests) == 1
    assert requests[0].action == "tool.publish_report"
