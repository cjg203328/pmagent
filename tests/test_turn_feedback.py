"""Tests for the chat feedback loop (Phase 1 deepening).

Covers:
  * EpisodeStore.set_feedback tags a turn's episode.
  * record_turn_feedback writes to BOTH FeedbackStore (injected next turn)
    and the Episode.feedback column (mined by ReflectionJob).
  * Bare negatives are recorded but flagged no_inject so they don't
    pollute every future turn's context.
  * format_feedback_context skips no_inject entries.
"""

import pytest

from artpm_agent.harness.memory_retrieval import (
    format_feedback_context,
    record_turn_feedback,
)
from artpm_agent.harness.outcome_recorder import default_episode_db_path
from artpm_agent.memory.episode_store import Episode, EpisodeStore
from artpm_agent.memory.feedback_store import (
    KIND_AVOID,
    KIND_PREFERENCE,
    FeedbackEntry,
    get_default_feedback_store,
)


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path, monkeypatch):
    """Point the default stores at tmp files so tests never touch data/*.db."""
    fb = str(tmp_path / "feedback.db")
    ep = str(tmp_path / "episodes.db")
    monkeypatch.setenv("ARTPM_FEEDBACK_DB", fb)
    monkeypatch.setenv("ARTPM_EPISODE_DB", ep)
    import artpm_agent.memory.feedback_store as fbs
    import artpm_agent.harness.outcome_recorder as ocr

    fbs._DEFAULT_STORE = None
    ocr._DEFAULT_STORE = None
    yield


def _seed_episode(ep_store: EpisodeStore, turn_id: str, *, success: bool = True) -> None:
    ep_store.record(
        Episode(
            turn_id=turn_id,
            conversation_id="conv-1",
            handler="model",
            success=success,
        )
    )


def test_set_feedback_tags_episode(tmp_path):
    store = EpisodeStore(str(tmp_path / "ep.db"))
    _seed_episode(store, "t1")
    assert store.set_feedback("t1", "👎 用户不满意") == 1
    assert store.recent(limit=10)[0].feedback == "👎 用户不满意"
    # Unknown turn id touches nothing.
    assert store.set_feedback("nope", "x") == 0


def test_positive_records_preference_and_tags_episode():
    ep = EpisodeStore(default_episode_db_path())
    _seed_episode(ep, "t-up")
    res = record_turn_feedback(
        "t-up", True, user_prompt="你好", assistant_content="你好！"
    )
    assert res["feedback_id"]
    assert res["episode_updated"] >= 1

    entries = get_default_feedback_store().active()
    assert len(entries) == 1
    assert entries[0].kind == KIND_PREFERENCE
    # Positive signals are always injected.
    assert not entries[0].metadata.get("no_inject")
    # The episode is tagged so reflection can mine it.
    assert "👍" in ep.recent(limit=10)[0].feedback


def test_negative_with_reason_stores_avoid_and_injects():
    ep = EpisodeStore(default_episode_db_path())
    _seed_episode(ep, "t-down")
    res = record_turn_feedback("t-down", False, correction="回答太啰嗦")
    assert res["episode_updated"] >= 1

    entries = get_default_feedback_store().active()
    assert entries[0].kind == KIND_AVOID
    assert entries[0].content == "回答太啰嗦"
    # A concrete user-authored reason becomes an injected avoid preference.
    assert not entries[0].metadata.get("no_inject")
    assert "👎 用户反馈（回合 t-down）：回答太啰嗦" in ep.recent(10)[0].feedback


def test_negative_without_reason_marks_no_inject():
    ep = EpisodeStore(default_episode_db_path())
    _seed_episode(ep, "t-down2")
    record_turn_feedback("t-down2", False)

    entries = get_default_feedback_store().active()
    assert entries[0].kind == KIND_AVOID
    # Bare negative: recorded for reflection but NOT injected as noise.
    assert entries[0].metadata.get("no_inject") is True


def test_format_skips_no_inject():
    good = FeedbackEntry(
        kind=KIND_PREFERENCE, content="用户偏好简洁", scope="global"
    )
    noisy = FeedbackEntry(
        kind=KIND_AVOID,
        content="用户不满意",
        scope="global",
        metadata={"no_inject": True},
    )
    block = format_feedback_context([good, noisy])
    assert "【用户偏好与纠正】" in block
    assert "用户偏好简洁" in block
    assert "用户不满意" not in block
