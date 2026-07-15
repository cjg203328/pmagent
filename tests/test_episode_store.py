"""Tests for the Phase 0 episodic outcome memory (evolution data foundation)."""

import pytest

from artpm_agent.harness.outcome_recorder import (
    episode_from_turn,
    record_outcome,
    run_turn_recorded,
)
from artpm_agent.harness.turn_service import TurnContext, TurnResult
from artpm_agent.memory.episode_store import Episode, EpisodeStore


def _ctx(turn_id="t1", conv="c1", text="报个价"):
    return TurnContext(turn_id=turn_id, conversation_id=conv, user_input=text)


def _result(handler="skill", success=True, error=None, awaiting=False):
    return TurnResult(
        response="ok",
        handled_by=handler,
        success=success,
        error=error,
        awaiting_approval=awaiting,
    )


def test_record_and_recent_roundtrip(tmp_path):
    store = EpisodeStore(str(tmp_path / "ep.db"))
    ep_id = store.record(
        Episode(
            turn_id="t1",
            conversation_id="c1",
            handler="skill",
            success=True,
            user_input_excerpt="报价",
        )
    )
    assert ep_id
    recent = store.recent(limit=10)
    assert len(recent) == 1
    assert recent[0].turn_id == "t1"
    assert recent[0].handler == "skill"
    assert recent[0].success is True
    assert recent[0].user_input_excerpt == "报价"


def test_failure_rate(tmp_path):
    store = EpisodeStore(str(tmp_path / "ep.db"))
    for i in range(3):
        store.record(
            Episode(
                turn_id=f"t{i}",
                conversation_id="c",
                handler="skill",
                success=(i != 0),  # one failure out of three
            )
        )
    store.record(
        Episode(turn_id="x", conversation_id="c", handler="model", success=False)
    )
    assert store.failure_rate() == pytest.approx(2 / 4)
    assert store.failure_rate(handler="skill") == pytest.approx(1 / 3)
    assert store.failure_rate(handler="model") == pytest.approx(1.0)


def test_recent_feedback_filters(tmp_path):
    store = EpisodeStore(str(tmp_path / "ep.db"))
    store.record(
        Episode(turn_id="a", conversation_id="c", handler="skill", success=True, feedback="别用旧模板")
    )
    store.record(
        Episode(turn_id="b", conversation_id="c", handler="skill", success=True)
    )
    feedback = store.recent_feedback(limit=10)
    assert len(feedback) == 1
    assert feedback[0].feedback == "别用旧模板"


def test_episode_from_turn_classifies_errors(tmp_path):
    ok = episode_from_turn(_ctx(), _result(success=True))
    assert ok.error_kind is None

    vision = episode_from_turn(_ctx(), _result(success=False, error="vision capability error"))
    assert vision.error_kind == "vision"

    timeout = episode_from_turn(_ctx(), _result(success=False, error="request timed out"))
    assert timeout.error_kind == "timeout"

    model = episode_from_turn(_ctx(), _result(success=False, error="model api key invalid"))
    assert model.error_kind == "model"

    unknown = episode_from_turn(_ctx(), _result(success=False, error="boom"))
    assert unknown.error_kind == "unknown"


def test_record_outcome_is_best_effort(tmp_path):
    store = EpisodeStore(str(tmp_path / "ep.db"))
    # Normal path records and returns an id.
    ep_id = record_outcome(_ctx(), _result(), store=store)
    assert ep_id
    # A store whose record raises must NOT propagate.
    class BrokenStore:
        def record(self, episode):
            raise RuntimeError("disk full")

    assert record_outcome(_ctx(), _result(), store=BrokenStore()) is None


def test_run_turn_recorded_records_outcome(tmp_path, monkeypatch):
    store = EpisodeStore(str(tmp_path / "ep.db"))

    captured = {}

    def fake_run_turn(ctx, **kw):
        captured["ctx"] = ctx
        return _result(handler="skill", success=True)

    monkeypatch.setattr(
        "artpm_agent.harness.turn_service.run_turn", fake_run_turn
    )

    result = run_turn_recorded(_ctx(), store=store)
    assert result.handled_by == "skill"
    assert len(store.recent()) == 1
    assert store.recent()[0].turn_id == "t1"
