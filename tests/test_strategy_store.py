"""Tests for the learned-strategy store."""
from artpm_agent.evolution.strategy_store import (
    Strategy,
    StrategyStore,
    default_strategies_db_path,
    get_default_strategy_store,
)


def test_add_and_active(tmp_path):
    store = StrategyStore(str(tmp_path / "st.db"))
    sid = store.add(
        Strategy(capability="skill_handler", rule_text="失败率高时提前回退")
    )
    assert sid
    active = store.active()
    assert len(active) == 1
    assert active[0].capability == "skill_handler"


def test_capability_filter(tmp_path):
    store = StrategyStore(str(tmp_path / "st.db"))
    store.add(Strategy(capability="global", rule_text="g"))
    store.add(Strategy(capability="skill_handler", rule_text="s"))
    scoped = store.active(capability="skill_handler")
    assert len(scoped) == 2  # global + skill_handler
    only_global = store.active(capability="workflow_handler")
    assert len(only_global) == 1


def test_deactivate_and_hit(tmp_path):
    store = StrategyStore(str(tmp_path / "st.db"))
    sid = store.add(Strategy(capability="global", rule_text="g"))
    store.record_hit(sid)
    assert store.get(sid).hit_count == 1
    assert store.deactivate(sid) is True
    assert store.active() == []


def test_default_path_and_lazy_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTPM_STRATEGY_DB", str(tmp_path / "st.db"))
    assert default_strategies_db_path().endswith("st.db")
    store = get_default_strategy_store()
    assert store is not None
    assert store.add(Strategy(capability="global", rule_text="ok"))
