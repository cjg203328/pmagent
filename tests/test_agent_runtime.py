from __future__ import annotations

from types import MethodType

import pytest

from artpm_agent.agent import ArtPMAgent
from artpm_agent.runtime import (
    AgentEventType,
    AgentRunAbortedError,
    AgentRunBusyError,
    AgentRuntime,
    AgentSubscriberError,
)


def test_runtime_emits_pi_style_event_order_and_tracks_state():
    runtime = AgentRuntime()

    events = list(
        runtime.run(
            "hello",
            lambda _prompt, _context: iter(("hel", "lo")),
            run_id="run-1",
            turn_id="turn-1",
        )
    )

    assert [event.type for event in events] == [
        AgentEventType.AGENT_START,
        AgentEventType.TURN_START,
        AgentEventType.MESSAGE_START,
        AgentEventType.MESSAGE_END,
        AgentEventType.MESSAGE_START,
        AgentEventType.MESSAGE_UPDATE,
        AgentEventType.MESSAGE_UPDATE,
        AgentEventType.MESSAGE_END,
        AgentEventType.TURN_END,
        AgentEventType.AGENT_END,
    ]
    assert [event.delta for event in events if event.delta] == ["hel", "lo"]
    assert events[-1].message is not None
    assert events[-1].message.content == "hello"
    assert [message.role for message in runtime.state.messages] == [
        "user",
        "assistant",
    ]
    assert not runtime.state.is_running


def test_runtime_transforms_context_and_supports_observers():
    seen_context = {}
    observed = []
    runtime = AgentRuntime(
        context_transform=lambda context: {**context, "injected": True}
    )
    unsubscribe = runtime.subscribe(observed.append)

    def responder(_prompt, context):
        seen_context.update(context)
        return "done"

    list(runtime.run("work", responder, context={"source": "test"}))
    unsubscribe()
    list(runtime.run("again", responder))

    assert seen_context["injected"] is True
    assert seen_context.get("source") == "test"
    assert observed
    assert observed[0].type == AgentEventType.AGENT_START
    assert len(observed) == 9


def test_runtime_emits_terminal_error_events_and_recovers():
    observed = []
    runtime = AgentRuntime()
    runtime.subscribe(observed.append)

    def failing_responder(_prompt, _context):
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        list(runtime.run("hello", failing_responder))

    assert [event.type for event in observed[-4:]] == [
        AgentEventType.RUNTIME_ERROR,
        AgentEventType.MESSAGE_END,
        AgentEventType.TURN_END,
        AgentEventType.AGENT_END,
    ]
    assert runtime.state.error == "provider unavailable"
    assert runtime.state.messages[-1].status == "error"
    assistant_start = next(
        event
        for event in observed
        if event.type == AgentEventType.MESSAGE_START
        and event.message is not None
        and event.message.role == "assistant"
    )
    assert observed[-3].message is not None
    assert observed[-3].message.id == assistant_start.message.id
    assert observed[-3].message.content == "provider unavailable"
    assert not runtime.state.is_running

    assert [
        event.delta for event in runtime.run("retry", lambda *_: "ok") if event.delta
    ] == ["ok"]


def test_runtime_rejects_overlapping_runs_and_releases_on_close():
    runtime = AgentRuntime()
    first = runtime.run("first", lambda *_: "done")
    assert next(first).type == AgentEventType.AGENT_START

    second = runtime.run("second", lambda *_: "done")
    with pytest.raises(AgentRunBusyError):
        next(second)

    first.close()
    assert not runtime.state.is_running


def test_runtime_abort_is_cooperative_and_emits_error_terminal_events():
    observed = []
    runtime = AgentRuntime()
    runtime.subscribe(observed.append)

    def responder(_prompt, _context):
        yield "first"
        yield "second"

    stream = runtime.run("hello", responder)
    first_delta = next(
        event for event in stream if event.type == AgentEventType.MESSAGE_UPDATE
    )
    assert first_delta.delta == "first"
    assert runtime.abort()

    with pytest.raises(AgentRunAbortedError, match="agent run aborted"):
        list(stream)

    assert observed[-1].type == AgentEventType.AGENT_END
    assert runtime.state.messages[-1].status == "error"
    assert not runtime.state.is_running


def test_noncritical_subscriber_failure_does_not_fail_run():
    runtime = AgentRuntime()
    runtime.subscribe(
        lambda _event: (_ for _ in ()).throw(OSError("metrics unavailable")),
        critical=False,
    )

    events = list(runtime.run("hello", lambda *_: "done"))

    assert events[-1].type == AgentEventType.AGENT_END
    assert runtime.state.error is None


def test_runtime_clears_in_memory_transcript_when_session_scope_changes():
    runtime = AgentRuntime()

    list(runtime.run("one", lambda *_: "A", session_id="workspace:chat-1"))
    list(runtime.run("two", lambda *_: "B", session_id="workspace:chat-1"))
    assert [message.content for message in runtime.state.messages] == [
        "one",
        "A",
        "two",
        "B",
    ]

    list(
        runtime.run(
            "other",
            lambda *_: "C",
            session_id="workspace:chat-2",
            metadata={"conversation_id": "chat-2"},
        )
    )

    assert runtime.state.session_id == "workspace:chat-2"
    assert [message.content for message in runtime.state.messages] == ["other", "C"]
    assert all(
        message.metadata["conversation_id"] == "chat-2"
        for message in runtime.state.messages
    )


def test_context_transform_failure_keeps_lifecycle_ordering():
    observed = []
    runtime = AgentRuntime(
        context_transform=lambda _context: (_ for _ in ()).throw(
            ValueError("bad context")
        )
    )
    runtime.subscribe(observed.append)

    with pytest.raises(ValueError, match="bad context"):
        list(runtime.run("hello", lambda *_: "unused"))

    assert [event.type for event in observed[:5]] == [
        AgentEventType.AGENT_START,
        AgentEventType.TURN_START,
        AgentEventType.MESSAGE_START,
        AgentEventType.MESSAGE_END,
        AgentEventType.MESSAGE_START,
    ]
    assert [event.type for event in observed[-4:]] == [
        AgentEventType.RUNTIME_ERROR,
        AgentEventType.MESSAGE_END,
        AgentEventType.TURN_END,
        AgentEventType.AGENT_END,
    ]


def test_critical_subscriber_failure_fails_run_but_runtime_settles():
    runtime = AgentRuntime()

    def broken_subscriber(_event):
        raise OSError("audit store unavailable")

    runtime.subscribe(broken_subscriber)
    with pytest.raises(AgentSubscriberError, match="critical subscriber failed"):
        list(runtime.run("hello", lambda *_: "done"))

    assert not runtime.state.is_running
    assert runtime.state.error == "critical subscriber failed for agent_start"


def test_assistant_end_barrier_failure_replaces_message_without_duplicate_id():
    runtime = AgentRuntime()

    def fail_completed_assistant(event):
        if (
            event.type == AgentEventType.MESSAGE_END
            and event.message is not None
            and event.message.role == "assistant"
            and event.message.status == "complete"
        ):
            raise OSError("transcript write failed")

    runtime.subscribe(fail_completed_assistant)
    with pytest.raises(AgentSubscriberError, match="critical subscriber failed"):
        list(runtime.run("hello", lambda *_: "done"))

    state = runtime.state
    message_ids = [message.id for message in state.messages]
    assert len(message_ids) == len(set(message_ids))
    assert [message.role for message in state.messages] == ["user", "assistant"]
    assert state.messages[-1].status == "error"
    assert state.messages[-1].content == "done"


def test_artpm_agent_stream_adapter_preserves_deltas_and_turn_identity():
    agent = ArtPMAgent.__new__(ArtPMAgent)
    agent.runtime = AgentRuntime()
    agent._primary_model_id = MethodType(lambda _self: "test-model", agent)
    agent._stream_response_chunks = MethodType(
        lambda _self, _prompt, _context=None: iter(("A", "B")),
        agent,
    )

    events = list(
        agent.stream_events(
            "hello",
            context={
                "conversation_id": "conversation-1",
                "workspace_id": "workspace-1",
                "turn_id": "turn-1",
            },
        )
    )

    assert {event.turn_id for event in events} == {"turn-1"}
    assert events[0].metadata["conversation_id"] == "conversation-1"
    assert events[0].metadata["requested_model_id"] == "test-model"
    assert list(agent.stream_chat("again")) == ["A", "B"]
