"""Tests for the session query surface (search / lineage / summary / bounded)."""

from __future__ import annotations

import pytest

from artpm_agent.runtime.event_bus import SessionEventLog
from artpm_agent.runtime.events import AgentEvent, AgentEventType, EventDomain
from artpm_agent.runtime.session_query import SessionQuery


def _log(tmp_path, events):
    log = SessionEventLog(tmp_path / "session.jsonl")
    for event in events:
        log.append(event)
    return log


def _event(
    event_type,
    *,
    run_id="run-1",
    turn_id="turn-1",
    message=None,
    tool_name=None,
    error=None,
    is_error=False,
):
    return AgentEvent(
        type=event_type,
        run_id=run_id,
        turn_id=turn_id,
        domain=EventDomain.SESSION,
        message=message,
        tool_name=tool_name,
        error=error,
        is_error=is_error,
    )


def test_search_finds_text_in_message_content(tmp_path):
    from artpm_agent.runtime.events import AgentMessage

    log = _log(
        tmp_path,
        [
            _event(
                AgentEventType.MESSAGE_END,
                message=AgentMessage(role="assistant", content="报价测算完成，成本 1200 元"),
            ),
            _event(
                AgentEventType.MESSAGE_END,
                message=AgentMessage(role="assistant", content="任务分配完成"),
            ),
        ],
    )
    query = SessionQuery(log)

    matches = query.search("报价")

    assert len(matches) == 1
    assert "报价测算完成" in matches[0]["message"]["content"]


def test_search_is_case_insensitive_and_bounded(tmp_path):
    from artpm_agent.runtime.events import AgentMessage

    log = _log(
        tmp_path,
        [
            _event(
                AgentEventType.MESSAGE_END,
                message=AgentMessage(role="user", content=f"payload-{index}"),
            )
            for index in range(10)
        ],
    )
    query = SessionQuery(log)

    matches = query.search("PAYLOAD", limit=3)

    assert len(matches) == 3
    assert matches[-1]["message"]["content"] == "payload-9"  # newest kept


def test_search_rejects_empty_text(tmp_path):
    query = SessionQuery(_log(tmp_path, []))
    with pytest.raises(ValueError, match="non-empty"):
        query.search("   ")


def test_lineage_groups_by_turn_in_order(tmp_path):
    from artpm_agent.runtime.events import AgentMessage

    log = _log(
        tmp_path,
        [
            _event(AgentEventType.AGENT_START, run_id="run-9", turn_id="turn-0"),
            _event(AgentEventType.TURN_START, run_id="run-9", turn_id="turn-1"),
            _event(
                AgentEventType.MESSAGE_END,
                run_id="run-9",
                turn_id="turn-1",
                message=AgentMessage(role="assistant", content="a"),
            ),
            _event(AgentEventType.TOOL_EXECUTION_END, run_id="run-9", turn_id="turn-1", tool_name="quote"),
            _event(AgentEventType.TURN_END, run_id="run-9", turn_id="turn-1"),
            _event(AgentEventType.TURN_START, run_id="run-9", turn_id="turn-2"),
            _event(AgentEventType.TURN_END, run_id="run-9", turn_id="turn-2"),
            _event(AgentEventType.AGENT_END, run_id="run-9", turn_id="turn-0"),
        ],
    )
    query = SessionQuery(log)

    lineage = query.lineage("run-9")

    assert lineage.run_id == "run-9"
    assert [turn.turn_id for turn in lineage.turns] == [
        "turn-0", "turn-1", "turn-2",
    ]
    assert lineage.event_count == 8
    turn_1 = lineage.turns[1]
    assert [event["type"] for event in turn_1.events] == [
        "turn_start", "message_end", "tool_execution_end", "turn_end",
    ]


def test_lineage_limit_per_turn_keeps_tail(tmp_path):
    log = _log(
        tmp_path,
        [
            _event(AgentEventType.MESSAGE_END, run_id="r", turn_id="t", error=None),
            _event(AgentEventType.MESSAGE_END, run_id="r", turn_id="t"),
            _event(AgentEventType.MESSAGE_END, run_id="r", turn_id="t"),
        ],
    )
    query = SessionQuery(log)

    lineage = query.lineage("r", limit_per_turn=1)

    assert len(lineage.turns[0].events) == 1


def test_summary_counts_messages_tools_and_errors(tmp_path):
    from artpm_agent.runtime.events import AgentMessage

    log = _log(
        tmp_path,
        [
            _event(AgentEventType.AGENT_START, run_id="r"),
            _event(
                AgentEventType.MESSAGE_END,
                run_id="r",
                message=AgentMessage(role="assistant", content="x"),
            ),
            _event(
                AgentEventType.MESSAGE_END,
                run_id="r",
                message=AgentMessage(role="assistant", content="y"),
            ),
            _event(AgentEventType.TOOL_EXECUTION_END, run_id="r", tool_name="t1"),
            _event(
                AgentEventType.TOOL_EXECUTION_END,
                run_id="r",
                tool_name="t2",
                is_error=True,
            ),
            _event(AgentEventType.AGENT_END, run_id="r"),
        ],
    )
    query = SessionQuery(log)

    summary = query.summary("r")

    assert summary["event_count"] == 6
    assert summary["message_count"] == 2
    assert summary["tool_call_count"] == 2
    assert summary["error_count"] == 1
    assert summary["first_event_at"] is not None
    assert summary["last_event_at"] is not None


def test_summary_empty_run(tmp_path):
    query = SessionQuery(_log(tmp_path, []))
    summary = query.summary("ghost")
    assert summary["event_count"] == 0
    assert summary["first_event_at"] is None


def test_bounded_read_limits_events_and_chars(tmp_path):
    from artpm_agent.runtime.events import AgentMessage

    long_content = "x" * 5_000
    log = _log(
        tmp_path,
        [
            _event(
                AgentEventType.MESSAGE_END,
                run_id="r",
                message=AgentMessage(role="assistant", content=long_content),
            )
            for _ in range(5)
        ],
    )
    query = SessionQuery(log)

    by_events = query.bounded_read("r", max_events=2)
    assert len(by_events) == 2

    by_chars = query.bounded_read("r", max_chars=12_000)
    # First event alone is 5k chars, second would exceed the 12k budget.
    assert len(by_chars) == 2


def test_bounded_read_stops_when_first_event_exceeds_budget(tmp_path):
    from artpm_agent.runtime.events import AgentMessage

    log = _log(
        tmp_path,
        [
            _event(
                AgentEventType.MESSAGE_END,
                run_id="r",
                message=AgentMessage(role="assistant", content="y" * 1_000),
            ),
        ],
    )
    query = SessionQuery(log)
    # Budget smaller than the single event: still returns it (never empty
    # unless there are truly no events), then stops.
    read = query.bounded_read("r", max_chars=10)
    assert len(read) == 1
