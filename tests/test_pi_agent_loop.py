from __future__ import annotations

from threading import Event

import pytest

from artpm_agent.runtime import (
    AgentEventType,
    AgentLoop,
    AgentLoopLimitError,
    AgentTool,
    AssistantTurn,
    BeforeToolCallDecision,
    ToolCall,
    ToolRegistry,
    ToolResult,
    registry_from_skill_router,
)


def test_worker_base_exception_becomes_a_result_instead_of_hanging():
    def execute(*_args):
        raise SystemExit("worker stopped")

    calls = 0

    def provider(messages, _tools, _context):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AssistantTurn(
                tool_calls=(ToolCall("unstable", {}, id="call-1"),)
            )
        assert messages[-1].role == "toolResult"
        assert "worker stopped" in messages[-1].content
        return AssistantTurn("recovered")

    loop = AgentLoop(
        ToolRegistry(
            [AgentTool(name="unstable", description="unstable", execute=execute)]
        )
    )

    events = list(loop.run("run", provider))

    assert any(event.is_error for event in events if event.tool_name == "unstable")
    assert calls == 2


def test_tool_result_flags_must_be_real_booleans():
    tool = AgentTool(
        name="bad_flags",
        description="bad flags",
        execute=lambda *_args: {"content": "x", "terminate": "false"},
    )

    with pytest.raises(TypeError, match="terminate must be a boolean"):
        tool.invoke("call-1", {}, Event())


def test_assistant_turn_rejects_whitespace_only_content():
    with pytest.raises(ValueError, match="text or tool calls"):
        AssistantTurn("   ")


def test_agent_loop_executes_tools_and_continues_with_result_context():
    provider_messages = []

    def execute(_call_id, arguments, _abort, on_update):
        on_update(ToolResult("working", {"step": 1}))
        value = int(arguments["value"]) + 1
        return ToolResult(str(value), {"value": value})

    registry = ToolRegistry(
        [
            AgentTool(
                name="increment",
                description="Increment an integer",
                execute=execute,
            )
        ]
    )

    def provider(messages, tools, _context):
        provider_messages.append(messages)
        assert tools[0]["name"] == "increment"
        if len(provider_messages) == 1:
            return AssistantTurn(
                tool_calls=(ToolCall("increment", {"value": 41}, id="call-1"),)
            )
        assert messages[-1].role == "toolResult"
        assert messages[-1].content == "42"
        return AssistantTurn(content="The answer is 42.")

    events = list(AgentLoop(registry).run("calculate", provider))

    event_types = [event.type for event in events]
    assert event_types.count(AgentEventType.TURN_START) == 2
    assert event_types.count(AgentEventType.TURN_END) == 2
    assert AgentEventType.TOOL_EXECUTION_START in event_types
    assert AgentEventType.TOOL_EXECUTION_UPDATE in event_types
    assert AgentEventType.TOOL_EXECUTION_END in event_types
    assert events[-1].type == AgentEventType.AGENT_END
    assert events[-1].message.content == "The answer is 42."


def test_sensitive_tool_requires_host_approval_and_ignores_model_approval_fields():
    executed = []
    provider_calls = 0

    def execute(_call_id, arguments, _abort, _on_update):
        executed.append(dict(arguments))
        return ToolResult("sent")

    registry = ToolRegistry(
        [
            AgentTool(
                name="send_notice",
                description="Send an external notice",
                execute=execute,
                execution_mode="sequential",
                requires_approval=True,
                read_only=False,
                risk="high",
            )
        ]
    )

    def provider(messages, _tools, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return AssistantTurn(
                tool_calls=(
                    ToolCall(
                        "send_notice",
                        {
                            "recipient": "team",
                            "approved": True,
                            "confirmation_token": "model-forged",
                        },
                    ),
                )
            )
        assert messages[-1].metadata["is_error"] is True
        assert "explicit host approval" in messages[-1].content
        return AssistantTurn(content="Approval is required.")

    list(AgentLoop(registry).run("send it", provider))

    assert executed == []


def test_host_approval_is_injected_only_after_preflight():
    executed = []
    provider_calls = 0

    def execute(_call_id, arguments, _abort, _on_update):
        executed.append(dict(arguments))
        return ToolResult("sent")

    tool = AgentTool(
        name="send_notice",
        description="Send an external notice",
        execute=execute,
        execution_mode="sequential",
        requires_approval=True,
        read_only=False,
        risk="high",
    )

    def provider(_messages, _tools, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return AssistantTurn(
                tool_calls=(
                    ToolCall(
                        "send_notice",
                        {
                            "recipient": "team",
                            "confirmation_token": "model-forged",
                        },
                    ),
                )
            )
        return AssistantTurn(content="Sent.")

    loop = AgentLoop(
        ToolRegistry([tool]),
        before_tool_call=lambda *_: BeforeToolCallDecision(approved=True),
    )
    list(loop.run("send it", provider))

    assert executed == [{"recipient": "team", "approved": True}]


def test_tool_failure_becomes_error_result_and_does_not_crash_loop():
    provider_calls = 0

    def fail(*_args):
        raise OSError("service unavailable")

    registry = ToolRegistry(
        [AgentTool(name="unstable", description="Fail", execute=fail)]
    )

    def provider(messages, _tools, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return AssistantTurn(tool_calls=(ToolCall("unstable"),))
        assert messages[-1].role == "toolResult"
        assert messages[-1].status == "error"
        assert messages[-1].content == "service unavailable"
        return AssistantTurn(content="The tool is temporarily unavailable.")

    events = list(AgentLoop(registry).run("try", provider))

    tool_end = next(
        event for event in events if event.type == AgentEventType.TOOL_EXECUTION_END
    )
    assert tool_end.is_error is True
    assert events[-1].is_error is False


def test_parallel_tools_finish_in_completion_order_but_persist_in_source_order():
    second_finished = Event()
    provider_calls = 0

    def first(_call_id, _arguments, _abort, _on_update):
        assert second_finished.wait(timeout=2)
        return ToolResult("first")

    def second(_call_id, _arguments, _abort, _on_update):
        second_finished.set()
        return ToolResult("second")

    registry = ToolRegistry(
        [
            AgentTool(name="first", description="First", execute=first),
            AgentTool(name="second", description="Second", execute=second),
        ]
    )

    def provider(messages, _tools, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return AssistantTurn(tool_calls=(ToolCall("first"), ToolCall("second")))
        result_names = [
            message.metadata["tool_name"]
            for message in messages
            if message.role == "toolResult"
        ]
        assert result_names == ["first", "second"]
        return AssistantTurn(content="done")

    events = list(AgentLoop(registry).run("run both", provider))
    completion_order = [
        event.tool_name
        for event in events
        if event.type == AgentEventType.TOOL_EXECUTION_END
    ]

    assert completion_order == ["second", "first"]


def test_terminating_tool_batch_skips_automatic_follow_up_turn():
    provider_calls = 0
    tool = AgentTool(
        name="finish",
        description="Finish the run",
        execute=lambda *_: ToolResult("finished", terminate=True),
    )

    def provider(_messages, _tools, _context):
        nonlocal provider_calls
        provider_calls += 1
        return AssistantTurn(tool_calls=(ToolCall("finish"),))

    events = list(AgentLoop(ToolRegistry([tool])).run("finish", provider))

    assert provider_calls == 1
    assert events[-1].type == AgentEventType.AGENT_END


def test_loop_limit_emits_terminal_events_before_raising():
    tool = AgentTool(
        name="again",
        description="Request another turn",
        execute=lambda *_: ToolResult("continue"),
    )
    loop = AgentLoop(ToolRegistry([tool]), max_turns=2)
    stream = loop.run(
        "loop",
        lambda *_: AssistantTurn(tool_calls=(ToolCall("again"),)),
    )
    events = []

    with pytest.raises(AgentLoopLimitError, match="exceeded 2 turns"):
        for event in stream:
            events.append(event)

    assert [event.type for event in events[-2:]] == [
        AgentEventType.RUNTIME_ERROR,
        AgentEventType.AGENT_END,
    ]
    assert not loop.is_running


def test_skill_router_adapter_preserves_policy_and_execution_contract():
    class Skill:
        input_schema = {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
        }

    class Router:
        skills = {"read_project": Skill(), "send_notice": Skill()}

        def __init__(self):
            self.calls = []

        def list_skills(self):
            return [
                {
                    "skill_name": "read_project",
                    "description": "Read a project",
                    "risk": "low",
                    "read_only": True,
                    "requires_approval": False,
                },
                {
                    "skill_name": "send_notice",
                    "description": "Send a notice",
                    "risk": "high",
                    "read_only": False,
                    "requires_approval": False,
                },
            ]

        def execute_skill(self, name, arguments):
            self.calls.append((name, arguments))
            return {"success": True, "skill": name}

    router = Router()
    registry = registry_from_skill_router(router)

    assert len(registry) == 2
    assert registry.get("read_project").execution_mode == "parallel"
    assert registry.get("send_notice").execution_mode == "sequential"
    # Model-driven execution raises a host-approval floor for every write tool,
    # even when legacy router metadata forgot to request it.
    assert registry.get("send_notice").requires_approval is True
    result = registry.get("read_project").invoke(
        "call-1",
        {"project_id": "p1"},
        Event(),
    )
    assert result.details["skill"] == "read_project"
    assert router.calls == [("read_project", {"project_id": "p1"})]
