from __future__ import annotations

from threading import Event, Lock, Thread
from time import monotonic, sleep

import pytest

from artpm_agent.runtime import (
    AgentEventType,
    AgentLoop,
    AgentLoopAbortedError,
    AgentLoopLimitError,
    AgentTool,
    AssistantTurn,
    BeforeToolCallDecision,
    ToolCall,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    load_spilled_result,
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


def test_worker_exit_without_a_result_is_finalized_instead_of_hanging():
    class BrokenExecutor(ToolExecutor):
        def _execute_worker(self, *_args):
            return None

    tool = AgentTool(name="broken", description="broken", execute=lambda *_: {})
    events = list(
        BrokenExecutor(ToolRegistry([tool]), tool_timeout_seconds=1).execute_batch(
            [ToolCall("broken")],
            run_id="run-broken",
            turn_id="turn-broken",
            context={},
            abort_event=Event(),
        )
    )

    result = next(
        event.tool_result
        for event in events
        if event.type == AgentEventType.TOOL_EXECUTION_END
    )
    assert result["is_error"] is True
    assert "worker exited" in result["content"]


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


def test_tool_executor_caps_parallel_workers():
    lock = Lock()
    active = 0
    peak = 0

    def execute(*_args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        sleep(0.03)
        with lock:
            active -= 1
        return ToolResult("done")

    registry = ToolRegistry(
        [
            AgentTool(name=f"tool_{index}", description="work", execute=execute)
            for index in range(5)
        ]
    )
    events = list(
        ToolExecutor(registry, max_workers=2).execute_batch(
            [ToolCall(f"tool_{index}") for index in range(5)],
            run_id="run-workers",
            turn_id="turn-workers",
            context={},
            abort_event=Event(),
        )
    )

    assert peak == 2
    assert sum(
        event.type == AgentEventType.TOOL_EXECUTION_END for event in events
    ) == 5


def test_tool_timeout_returns_error_and_signals_cooperative_cancellation():
    cancellation_seen = Event()

    def execute(_call_id, _arguments, abort_event, _on_update):
        abort_event.wait(timeout=1)
        if abort_event.is_set():
            cancellation_seen.set()
        return ToolResult("late")

    tool = AgentTool(name="slow", description="slow", execute=execute)
    started = monotonic()
    events = list(
        ToolExecutor(
            ToolRegistry([tool]),
            max_workers=1,
            tool_timeout_seconds=0.02,
        ).execute_batch(
            [ToolCall("slow")],
            run_id="run-timeout",
            turn_id="turn-timeout",
            context={},
            abort_event=Event(),
        )
    )
    elapsed = monotonic() - started
    tool_end = next(
        event for event in events if event.type == AgentEventType.TOOL_EXECUTION_END
    )

    assert elapsed < 0.5
    assert tool_end.is_error is True
    assert "timed out" in tool_end.tool_result["content"]
    assert cancellation_seen.wait(timeout=0.5)


def test_timed_out_worker_cannot_publish_updates_or_spills_after_return(tmp_path):
    worker_finished = Event()

    def execute(_call_id, _arguments, _abort_event, on_update):
        sleep(0.08)
        on_update(ToolResult("update" * 1_000))
        worker_finished.set()
        return ToolResult("final" * 1_000)

    events = list(
        ToolExecutor(
            ToolRegistry(
                [AgentTool(name="slow", description="slow", execute=execute)]
            ),
            tool_timeout_seconds=0.01,
            result_max_chars=512,
            spill_dir=tmp_path,
        ).execute_batch(
            [ToolCall("slow")],
            run_id="run-timeout",
            turn_id="turn-timeout",
            context={"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
            abort_event=Event(),
        )
    )

    assert worker_finished.wait(timeout=1)
    assert not any(
        event.type == AgentEventType.TOOL_EXECUTION_UPDATE for event in events
    )
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_late_worker_completion_cannot_publish_a_final_result_after_timeout(tmp_path):
    started = Event()
    emit = Event()
    update_called = Event()
    release = Event()
    worker_finished = Event()
    executor_returned = Event()
    output = {}

    def execute(_call_id, _arguments, _abort_event, on_update):
        started.set()
        emit.wait(timeout=1)
        update_called.set()
        on_update(ToolResult("update" * 1_000))
        release.wait(timeout=1)
        worker_finished.set()
        return ToolResult("final" * 1_000)

    executor = ToolExecutor(
        ToolRegistry(
            [AgentTool(name="late", description="late", execute=execute)]
        ),
        tool_timeout_seconds=0.03,
        result_max_chars=512,
        spill_dir=tmp_path,
    )

    def consume() -> None:
        output["events"] = list(
            executor.execute_batch(
                [ToolCall("late")],
                run_id="run-late",
                turn_id="turn-late",
                context={"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
                abort_event=Event(),
            )
        )
        executor_returned.set()

    thread = Thread(target=consume)
    thread.start()
    assert started.wait(timeout=1)
    emit.set()
    assert update_called.wait(timeout=1)
    assert executor_returned.wait(timeout=1)
    release.set()
    assert worker_finished.wait(timeout=1)
    thread.join(timeout=1)

    events = output["events"]
    ends = [
        event
        for event in events
        if event.type == AgentEventType.TOOL_EXECUTION_END
    ]
    assert len(ends) == 1
    assert ends[0].is_error is True
    assert "timed out" in ends[0].tool_result["content"]
    assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 1


def test_oversized_tool_update_is_bounded_and_spilled(tmp_path):
    update_text = "update" * 1_000
    provider_calls = 0

    def execute(_call_id, _arguments, _abort_event, on_update):
        on_update(ToolResult(update_text))
        return ToolResult("done")

    def provider(*_args):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return AssistantTurn(tool_calls=(ToolCall("report_progress"),))
        return AssistantTurn("complete")

    events = list(
        AgentLoop(
            ToolRegistry(
                [
                    AgentTool(
                        name="report_progress",
                        description="Report progress",
                        execute=execute,
                    )
                ]
            ),
            tool_result_max_chars=512,
            tool_spill_dir=tmp_path,
        ).run(
            "report progress",
            provider,
            context={"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        )
    )
    update = next(
        event
        for event in events
        if event.type == AgentEventType.TOOL_EXECUTION_UPDATE
    )

    assert len(update.tool_result["content"]) <= 512
    assert load_spilled_result(
        update.tool_result["details"]["spilled_path"],
        spill_dir=tmp_path,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    ) == update_text


def test_timeout_skips_tools_that_have_not_started():
    executed = []

    def slow(_call_id, _arguments, abort_event, _on_update):
        executed.append("slow")
        abort_event.wait(timeout=1)
        return ToolResult("late")

    def queued(*_args):
        executed.append("queued")
        return ToolResult("unexpected")

    registry = ToolRegistry(
        [
            AgentTool(name="slow", description="slow", execute=slow),
            AgentTool(name="queued", description="queued", execute=queued),
        ]
    )
    events = list(
        ToolExecutor(
            registry,
            max_workers=1,
            tool_timeout_seconds=0.02,
        ).execute_batch(
            [ToolCall("slow"), ToolCall("queued")],
            run_id="run-timeout",
            turn_id="turn-timeout",
            context={},
            abort_event=Event(),
        )
    )
    ends = {
        event.tool_name: event.tool_result["content"]
        for event in events
        if event.type == AgentEventType.TOOL_EXECUTION_END
    }

    assert executed == ["slow"]
    assert "timed out" in ends["slow"]
    assert "skipped" in ends["queued"]


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


def test_any_terminating_result_stops_a_mixed_batch():
    provider_calls = 0
    registry = ToolRegistry(
        [
            AgentTool(
                name="finish",
                description="Finish",
                execute=lambda *_: ToolResult("finished", terminate=True),
            ),
            AgentTool(
                name="observe",
                description="Observe",
                execute=lambda *_: ToolResult("observed"),
            ),
        ]
    )

    def provider(*_args):
        nonlocal provider_calls
        provider_calls += 1
        return AssistantTurn(
            tool_calls=(ToolCall("finish"), ToolCall("observe"))
        )

    events = list(AgentLoop(registry, max_turns=1).run("finish", provider))

    assert provider_calls == 1
    assert events[-1].type == AgentEventType.AGENT_END
    assert events[-1].is_error is False


def test_timeout_error_overrides_a_late_terminating_result():
    def slow(_call_id, _arguments, abort_event, _on_update):
        abort_event.wait(timeout=1)
        return ToolResult("slow result")

    def finish(_call_id, _arguments, _abort_event, _on_update):
        sleep(0.01)
        return ToolResult("finished", terminate=True)

    registry = ToolRegistry(
        [
            AgentTool(name="slow", description="Slow", execute=slow),
            AgentTool(name="finish", description="Finish", execute=finish),
        ]
    )
    loop = AgentLoop(
        registry,
        max_turns=1,
        max_tool_workers=2,
        tool_timeout_seconds=0.02,
    )

    events = []
    with pytest.raises(AgentLoopLimitError, match="exceeded 1 turns"):
        for event in loop.run(
            "finish",
            lambda *_: AssistantTurn(
                tool_calls=(ToolCall("slow"), ToolCall("finish"))
            ),
        ):
            events.append(event)

    assert any(
        event.tool_name == "slow" and event.is_error
        for event in events
        if event.type == AgentEventType.TOOL_EXECUTION_END
    )
    assert events[-1].is_error is True


def test_abort_during_tool_execution_cannot_become_successful_termination():
    started = Event()
    observed: list = []
    failure = []

    def execute(_call_id, _arguments, abort_event, _on_update):
        started.set()
        abort_event.wait(timeout=1)
        return ToolResult("late termination", terminate=True)

    loop = AgentLoop(
        ToolRegistry(
            [AgentTool(name="finish", description="Finish", execute=execute)]
        ),
        max_turns=1,
    )

    def consume() -> None:
        try:
            observed.extend(
                loop.run(
                    "finish",
                    lambda *_: AssistantTurn(tool_calls=(ToolCall("finish"),)),
                )
            )
        except BaseException as error:  # noqa: BLE001 - asserted below
            failure.append(error)

    thread = Thread(target=consume)
    thread.start()
    assert started.wait(timeout=1)
    assert loop.abort() is True
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert len(failure) == 1
    assert isinstance(failure[0], AgentLoopAbortedError)
    assert [event.type for event in observed[-2:]] == [
        AgentEventType.RUNTIME_ERROR,
        AgentEventType.AGENT_END,
    ]
    assert observed[-1].is_error is True


def test_abort_during_provider_rejects_a_late_model_answer():
    started = Event()
    release = Event()
    observed: list = []
    failure = []

    def provider(*_args):
        started.set()
        release.wait(timeout=1)
        return AssistantTurn("late answer")

    loop = AgentLoop()

    def consume() -> None:
        try:
            observed.extend(loop.run("answer", provider))
        except BaseException as error:  # noqa: BLE001 - asserted below
            failure.append(error)

    thread = Thread(target=consume)
    thread.start()
    assert started.wait(timeout=1)
    assert loop.abort() is True
    release.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert len(failure) == 1
    assert isinstance(failure[0], AgentLoopAbortedError)
    assert [event.type for event in observed[-2:]] == [
        AgentEventType.RUNTIME_ERROR,
        AgentEventType.AGENT_END,
    ]
    assert not any(
        event.message is not None and event.message.content == "late answer"
        for event in observed
    )


def test_abort_from_stop_hook_cannot_become_a_successful_stop():
    loop: AgentLoop

    def stop_after_turn(*_args):
        assert loop.abort() is True
        return True

    loop = AgentLoop(should_stop_after_turn=stop_after_turn)
    events = []

    with pytest.raises(AgentLoopAbortedError):
        for event in loop.run("answer", lambda *_: AssistantTurn("answer")):
            events.append(event)

    assert [event.type for event in events[-2:]] == [
        AgentEventType.RUNTIME_ERROR,
        AgentEventType.AGENT_END,
    ]
    assert events[-1].is_error is True


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


def test_final_turn_blocks_side_effects_before_loop_limit_error():
    executions = []
    tool = AgentTool(
        name="write",
        description="Write",
        execute=lambda *_: executions.append("write") or ToolResult("written"),
        read_only=False,
        execution_mode="sequential",
    )
    loop = AgentLoop(ToolRegistry([tool]), max_turns=2)

    with pytest.raises(AgentLoopLimitError, match="exceeded 2 turns"):
        list(
            loop.run(
                "write",
                lambda *_: AssistantTurn(tool_calls=(ToolCall("write"),)),
            )
        )

    assert executions == ["write"]


def test_cumulative_tool_budget_blocks_a_later_batch_before_execution():
    executions = []
    tool = AgentTool(
        name="read",
        description="Read",
        execute=lambda *_: executions.append("read") or ToolResult("read"),
    )
    loop = AgentLoop(
        ToolRegistry([tool]),
        max_turns=3,
        max_tool_calls_total=1,
    )

    with pytest.raises(AgentLoopLimitError, match="cumulative tool call budget"):
        list(
            loop.run(
                "read",
                lambda *_: AssistantTurn(tool_calls=(ToolCall("read"),)),
            )
        )

    assert executions == ["read"]


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


def test_skill_router_metadata_flags_fail_closed_when_not_booleans():
    executed = []

    class Skill:
        input_schema = {"type": "object"}

    class Router:
        skills = {"write_data": Skill()}

        @staticmethod
        def list_skills():
            return [
                {
                    "skill_name": "write_data",
                    "description": "Write data",
                    "read_only": "false",
                    "requires_approval": "false",
                    "is_plugin_skill": "false",
                }
            ]

        @staticmethod
        def execute_skill(_name, _arguments):
            executed.append("write")
            return {"success": True}

    tool = registry_from_skill_router(Router()).get("write_data")

    assert tool is not None
    assert tool.read_only is False
    assert tool.requires_approval is True
    assert tool.auto_approval_allowed is False
    events = list(
        ToolExecutor(ToolRegistry([tool])).execute_batch(
            [ToolCall("write_data")],
            run_id="run-metadata",
            turn_id="turn-metadata",
            context={},
            abort_event=Event(),
        )
    )
    result = next(
        event.tool_result
        for event in events
        if event.type == AgentEventType.TOOL_EXECUTION_END
    )

    assert result["is_error"] is True
    assert "requires explicit host approval" in result["content"]
    assert executed == []
