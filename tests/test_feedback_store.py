"""Tests for the feedback / preference memory store."""
from artpm_agent.memory.feedback_store import (
    FeedbackStore,
    default_feedback_db_path,
    get_default_feedback_store,
)


def test_add_and_active(tmp_path):
    store = FeedbackStore(str(tmp_path / "fb.db"))
    sid = store.add("preference", "回答要简洁")
    assert sid
    active = store.active()
    assert len(active) == 1
    assert active[0].content == "回答要简洁"
    assert active[0].active is True


def test_scope_filter(tmp_path):
    store = FeedbackStore(str(tmp_path / "fb.db"))
    store.add("preference", "全局偏好")
    store.add("avoid", "skill_x 不可用", scope="skill_handler")
    # global scope sees only the global entry
    glob = store.active(scope="global")
    assert len(glob) == 1
    # a specific scope sees its own entry + the global one
    scoped = store.active(scope="skill_handler")
    assert len(scoped) == 2


def test_deactivate(tmp_path):
    store = FeedbackStore(str(tmp_path / "fb.db"))
    sid = store.add("preference", "x")
    assert store.deactivate(sid) is True
    assert store.active() == []
    assert store.get(sid).active is False


def test_record_hit_bumps_weight(tmp_path):
    store = FeedbackStore(str(tmp_path / "fb.db"))
    sid = store.add("preference", "x", weight=1.0)
    store.record_hit(sid)
    assert store.get(sid).weight > 1.0


def test_default_path_and_lazy_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTPM_FEEDBACK_DB", str(tmp_path / "fb.db"))
    assert default_feedback_db_path().endswith("fb.db")
    store = get_default_feedback_store()
    assert store is not None
    assert store.add("preference", "ok")
