from __future__ import annotations

import logging

import pytest

from artpm_agent.agent import ArtPMAgent
from artpm_agent.harness import AgentSession, ModelToolCallsDisabledError
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.memory.session_store import SessionStore
from artpm_agent.runtime import (
    AgentLoop,
    AgentTool,
    AssistantTurn,
    ToolCall,
    ToolRegistry,
    ToolResult,
)
from artpm_agent.runtime.counters import runtime_counters


def _stores(tmp_path):
    conversations = ConversationStore(tmp_path / "conversations.db")
    conversation = conversations.create_conversation("Structured session")
    return conversation, SessionStore(conversations)


def test_session_gate_blocks_before_provider_or_persistence(tmp_path):
    conversation, store = _stores(tmp_path)
    provider_calls = []

    def provider(*args):
        provider_calls.append(args)
        return AssistantTurn("unexpected")

    session = AgentSession(
        AgentLoop(),
        provider,
        store,
        model_tool_calls_enabled=False,
    )

    with pytest.raises(ModelToolCallsDisabledError, match="disabled"):
        session.run("hello", conversation_id=conversation["id"])

    assert provider_calls == []
    assert store.replay(conversation["id"]) == []


def test_session_persists_every_event_before_exposing_it(tmp_path):
    conversation, store = _stores(tmp_path)
    provider_messages = []

    def execute(_call_id, arguments, _abort, _on_update):
        return ToolResult(
            content=f"found:{arguments['query']}",
            details={"count": 1},
        )

    registry = ToolRegistry(
        [
            AgentTool(
                name="lookup",
                description="Look up a project",
                parameters={
                    "type": "object",
                    "required": ["query"],
                    "properties": {"query": {"type": "string"}},
                    "additionalProperties": False,
                },
                execute=execute,
            )
        ]
    )

    def provider(messages, tools, _context):
        provider_messages.append(messages)
        assert tools[0]["name"] == "lookup"
        if len(provider_messages) == 1:
            return AssistantTurn(
                tool_calls=(ToolCall("lookup", {"query": "atlas"}, id="call-1"),),
                metadata={"provider": "fake", "stop_reason": "tool_use"},
            )
        assert messages[-1].role == "toolResult"
        return AssistantTurn(
            "done",
            metadata={"provider": "fake", "stop_reason": "stop"},
        )

    session = AgentSession(
        AgentLoop(registry),
        provider,
        store,
        model_tool_calls_enabled=True,
    )
    events = list(
        session.run(
            "find project",
            conversation_id=conversation["id"],
            run_id="run-1",
            turn_id="turn-1",
        )
    )

    entries = store.replay(conversation["id"])
    assert len(entries) == len(events)
    assert [entry.sequence for entry in entries] == list(range(1, len(entries) + 1))
    assert [entry.entry_type for entry in entries] == [
        event.type.value for event in events
    ]

    tool_end = next(
        entry for entry in entries if entry.entry_type == "tool_execution_end"
    )
    assert tool_end.tool_call == {
        "id": "call-1",
        "name": "lookup",
        "arguments": {"query": "atlas"},
    }
    assert tool_end.tool_result["content"] == "found:atlas"
    assert tool_end.tool_result["details"] == {"count": 1}

    assistant_entries = [
        entry
        for entry in entries
        if entry.entry_type == "message_end"
        and entry.message
        and entry.message["role"] == "assistant"
    ]
    assert assistant_entries[0].message["metadata"]["stop_reason"] == "tool_use"
    assert assistant_entries[-1].message["content"] == "done"
    assert len(provider_messages) == 2


def test_event_bus_failure_is_observable_without_breaking_durable_session(
    tmp_path,
    caplog,
):
    conversation, store = _stores(tmp_path)

    class FailingEventBus:
        def __init__(self):
            self.calls = 0

        def publish(self, _event):
            self.calls += 1
            raise RuntimeError("sink unavailable")

    event_bus = FailingEventBus()
    session = AgentSession(
        AgentLoop(),
        lambda *_: AssistantTurn("done"),
        store,
        model_tool_calls_enabled=True,
    )
    metric = "harness.agent_session.event_bus_publish_failures"
    before = runtime_counters.get(metric)

    with caplog.at_level(logging.WARNING, logger="artpm_agent.harness.agent_session"):
        events = list(
            session.run(
                "hello",
                conversation_id=conversation["id"],
                context={"event_bus": event_bus},
                run_id="run-event-bus",
                turn_id="turn-event-bus",
            )
        )

    assert len(store.replay(conversation["id"])) == len(events)
    assert event_bus.calls == len(events)
    assert runtime_counters.get(metric) - before == len(events)
    records = [
        record
        for record in caplog.records
        if record.message == "agent session event bus publish failed"
    ]
    assert len(records) == len(events)
    assert records[0].conversation_id == conversation["id"]
    assert records[0].event_type == "agent_start"
    assert records[0].run_id == "run-event-bus"
    assert records[0].turn_id == "turn-event-bus"


def test_session_injects_explicit_workspace_into_loop_context(tmp_path):
    conversation, store = _stores(tmp_path)
    provider_contexts = []

    def provider(_messages, _tools, context):
        provider_contexts.append(dict(context))
        return AssistantTurn("done")

    session = AgentSession(
        AgentLoop(),
        provider,
        store,
        model_tool_calls_enabled=True,
    )
    list(
        session.run(
            "hello",
            conversation_id=conversation["id"],
            workspace_id=ConversationStore.DEFAULT_WORKSPACE_ID,
            context={},
        )
    )

    assert provider_contexts == [
        {"workspace_id": ConversationStore.DEFAULT_WORKSPACE_ID}
    ]


def test_unknown_conversation_fails_before_calling_provider_and_releases_loop(
    tmp_path,
):
    _, store = _stores(tmp_path)
    provider_calls = []

    def provider(*args):
        provider_calls.append(args)
        return AssistantTurn("unexpected")

    loop = AgentLoop()
    session = AgentSession(
        loop,
        provider,
        store,
        model_tool_calls_enabled=True,
    )

    with pytest.raises(KeyError, match="Unknown conversation"):
        list(session.run("hello", conversation_id="missing"))

    assert provider_calls == []
    assert not loop.is_running


def test_agent_stream_events_wires_structured_session_only_when_gate_is_enabled(
    tmp_path,
):
    conversation, store = _stores(tmp_path)
    provider_contexts = []

    def provider(_messages, _tools, context):
        provider_contexts.append(dict(context))
        return AssistantTurn(
            "structured answer",
            metadata={"model": "fake-model", "stop_reason": "stop"},
        )

    class Config:
        values = {
            "agent_runtime.model_tool_calls_enabled": True,
            "agent_runtime.max_turns": 4,
            "agent_runtime.max_tool_calls_per_turn": 6,
        }

        def get(self, key, default=None):
            return self.values.get(key, default)

    class Gateway:
        @staticmethod
        def primary_model_id():
            return "fake-model"

    agent = object.__new__(ArtPMAgent)
    agent.config = Config()
    agent.model_gateway = Gateway()
    agent.structured_provider = provider
    agent._model_tool_session_store = store
    agent.tool_registry = ToolRegistry()
    agent._build_system_prompt = lambda *_args: "system prompt"

    events = list(
        agent.stream_events(
            "analyze this project",
            context={
                "conversation_id": conversation["id"],
                "workspace_id": ConversationStore.DEFAULT_WORKSPACE_ID,
                "turn_id": "turn-structured",
                "conversation_history": [
                    {"role": "user", "content": "prior question"},
                    {"role": "assistant", "content": "prior answer"},
                ],
            },
        )
    )

    assert any(
        event.message and event.message.content == "structured answer"
        for event in events
    )
    assert provider_contexts[0]["system_prompt"] == "system prompt"
    assert len(store.replay(conversation["id"])) == len(events)
