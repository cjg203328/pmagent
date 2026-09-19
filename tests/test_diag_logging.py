import logging

from artpm_agent.harness import AgentSession
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.memory.session_store import SessionStore
from artpm_agent.runtime import AgentLoop, AssistantTurn
from artpm_agent.runtime.counters import runtime_counters


def _stores(tmp_path):
    conversations = ConversationStore(tmp_path / "conversations.db")
    conversation = conversations.create_conversation("diag")
    return conversation, SessionStore(conversations)


def test_diag_caplog_state_after_mcp(caplog, tmp_path):
    root = logging.getLogger()
    print(
        "ROOT-HANDLERS:",
        [
            (h.__class__.__name__, getattr(h, "_artpm_managed", False))
            for h in root.handlers
        ],
    )
    print("ROOT-LEVEL:", root.level)

    class FailingEventBus:
        def __init__(self):
            self.calls = 0

        def publish(self, _event):
            self.calls += 1
            raise RuntimeError("sink unavailable")

    event_bus = FailingEventBus()
    conversation, store = _stores(tmp_path)
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

    print("EVENTS:", len(events))
    print("CALLS:", event_bus.calls)
    print("COUNTER:", runtime_counters.get(metric) - before)
    print("CAPLOG-RECORDS:", len(caplog.records))
    print(
        "MATCHING:",
        sum(
            1
            for r in caplog.records
            if r.message == "agent session event bus publish failed"
        ),
    )
