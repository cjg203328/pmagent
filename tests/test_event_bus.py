"""Tests for the three-domain event bus and the durable session event log."""

from __future__ import annotations

from artpm_agent.runtime.event_bus import (
    EventBus,
    SessionEventLog,
    build_bus_with_session_log,
)
from artpm_agent.runtime.events import AgentEvent, AgentEventType, EventDomain


def _event(
    event_type=AgentEventType.TURN_START,
    *,
    domain=EventDomain.AGENT,
    run_id="run-1",
    turn_id="turn-1",
):
    return AgentEvent(
        type=event_type,
        run_id=run_id,
        turn_id=turn_id,
        domain=domain,
    )


# ── publish / subscribe ──


def test_subscribe_and_publish_delivers():
    bus = EventBus()
    received = []
    bus.subscribe(lambda event: received.append(event.type.value))
    report = bus.publish(_event())

    assert report.delivered == 1
    assert not report.failed
    assert received == ["turn_start"]


def test_domain_filter_only_matches_same_domain():
    bus = EventBus()
    received = []
    bus.subscribe(lambda event: received.append(event), domain=EventDomain.SESSION)

    bus.publish(_event(domain=EventDomain.AGENT))
    bus.publish(_event(domain=EventDomain.SESSION))

    assert len(received) == 1
    assert received[0].domain is EventDomain.SESSION


def test_event_type_filter_and_unsubscribe():
    bus = EventBus()
    received = []
    unsubscribe = bus.subscribe(
        lambda event: received.append(event.type.value),
        event_type=AgentEventType.TURN_END,
    )

    bus.publish(_event())
    bus.publish(_event(AgentEventType.TURN_END))
    unsubscribe()
    bus.publish(_event(AgentEventType.TURN_END))

    assert received == ["turn_end"]
    assert bus.subscriber_count() == 0


def test_string_domain_and_type_acceptance():
    bus = EventBus()
    received = []
    bus.subscribe(lambda event: received.append(event), domain="session", event_type="agent_end")
    bus.publish(_event(AgentEventType.AGENT_END, domain=EventDomain.SESSION))
    assert len(received) == 1


def test_invalid_domain_rejected():
    bus = EventBus()
    try:
        bus.subscribe(lambda event: None, domain="nope")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid domain must raise")


def test_failing_subscriber_is_isolated():
    bus = EventBus()
    received = []

    def broken(event):
        raise RuntimeError("boom")

    bus.subscribe(broken)
    bus.subscribe(lambda event: received.append(event))

    report = bus.publish(_event())

    assert report.delivered == 1
    assert report.failed
    assert len(report.failures) == 1
    assert "boom" in report.failures[0][1]
    assert len(received) == 1


def test_failing_filter_does_not_drop_event():
    bus = EventBus()
    received = []

    def bad_filter(event):
        raise RuntimeError("filter boom")

    bus.subscribe(
        lambda event: received.append(event),
        filter=bad_filter,
    )
    bus.subscribe(lambda event: received.append(event))

    bus.publish(_event())

    assert len(received) == 2  # bad-filter subscriber still received


def test_sink_receives_every_event_and_is_isolated():
    bus = EventBus()
    seen = []

    def sink(event):
        seen.append(event)

    bus.attach_sink(sink)

    def broken_sink(event):
        raise RuntimeError("sink boom")

    bus.attach_sink(broken_sink)

    report = bus.publish(_event())

    assert len(seen) == 1
    assert report.failed  # broken sink reported, others unaffected


def test_sink_detach_removes_its_registration_and_is_idempotent():
    bus = EventBus()
    first_seen = []
    second_seen = []

    detach_first = bus.attach_sink(lambda event: first_seen.append(event))
    bus.attach_sink(lambda event: second_seen.append(event))

    detach_first()
    detach_first()
    bus.publish(_event())

    assert first_seen == []
    assert len(second_seen) == 1


def test_detaching_one_duplicate_sink_keeps_the_other_registration():
    bus = EventBus()
    seen = []

    def sink(event):
        seen.append(event)

    detach_first = bus.attach_sink(sink)
    bus.attach_sink(sink)

    detach_first()
    bus.publish(_event())

    assert len(seen) == 1


def test_forward_generator_filters_domains():
    bus = EventBus()
    received = []
    bus.subscribe(lambda event: received.append(event.domain.value))
    events = [
        _event(domain=EventDomain.AGENT),
        _event(domain=EventDomain.SESSION),
        _event(domain=EventDomain.CAPABILITY),
    ]
    published = bus.forward(iter(events), domains=(EventDomain.AGENT,))

    assert published == 1
    assert received == ["agent"]


def test_publish_rejects_non_event():
    bus = EventBus()
    try:
        bus.publish({"type": "turn_start"})
    except TypeError:
        pass
    else:
        raise AssertionError("publish must require an AgentEvent")


# ── SessionEventLog ──


def test_session_log_append_and_replay(tmp_path):
    log = SessionEventLog(tmp_path / "sessions" / "session-a.jsonl")
    log.append(_event(domain=EventDomain.SESSION))
    log.append(_event(AgentEventType.TURN_END, domain=EventDomain.SESSION, run_id="run-2"))
    log.append(_event(domain=EventDomain.AGENT))  # out-of-domain still stored raw

    records = log.replay(domain=EventDomain.SESSION)
    assert len(records) == 2
    assert [record["type"] for record in records] == ["turn_start", "turn_end"]
    assert records[0]["domain"] == "session"

    run_filtered = log.replay(domain="session", run_id="run-2")
    assert len(run_filtered) == 1
    assert run_filtered[0]["run_id"] == "run-2"

    type_filtered = log.replay(
        domain=EventDomain.SESSION, event_type=AgentEventType.TURN_START
    )
    assert len(type_filtered) == 1
    assert type_filtered[0]["type"] == "turn_start"
    assert type_filtered[0]["domain"] == "session"


def test_session_log_replay_limit_keeps_newest(tmp_path):
    log = SessionEventLog(tmp_path / "s.jsonl")
    for index in range(5):
        log.append(_event(domain=EventDomain.SESSION, run_id=f"run-{index}"))

    records = log.replay(domain=EventDomain.SESSION, limit=2)

    assert [record["run_id"] for record in records] == ["run-3", "run-4"]


def test_session_log_tolerates_corrupt_tail_line(tmp_path):
    path = tmp_path / "s.jsonl"
    log = SessionEventLog(path)
    log.append(_event(domain=EventDomain.SESSION, run_id="run-1"))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{corrupt-json\n")

    records = log.replay(domain=EventDomain.SESSION)

    assert len(records) == 1
    assert records[0]["run_id"] == "run-1"


def test_session_log_missing_file_replays_empty(tmp_path):
    log = SessionEventLog(tmp_path / "nope.jsonl")
    assert log.replay() == []
    assert log.event_count() == 0


def test_build_bus_with_session_log_persists_session_domain(tmp_path):
    path = tmp_path / "sessions" / "durable.jsonl"
    bus, log = build_bus_with_session_log(path)

    bus.publish(_event(domain=EventDomain.AGENT))
    bus.publish(_event(AgentEventType.TURN_END, domain=EventDomain.SESSION))

    records = log.replay()
    assert len(records) == 1
    assert records[0]["type"] == "turn_end"
    assert records[0]["domain"] == "session"
