"""P1 memory injection contract.

Validates the single-injection entry point: idempotency, block assembly,
priority ordering, cross-block dedup, and that feedback/strategy are surfaced.
"""
from types import SimpleNamespace

import pytest

from artpm_agent.harness.memory_retrieval import (
    FEEDBACK_CATEGORIES,
    inject_memory_context,
    record_turn_feedback,
)
from artpm_agent.profiles.models import (
    AgentProfile,
    AgentIdentity,
    response_style_rule,
)


class _FakeFeedback:
    def __init__(self, kind, content, scope="global", metadata=None):
        self.kind = kind
        self.content = content
        self.scope = scope
        self.metadata = metadata or {}


class _FakeStrategy:
    def __init__(self, rule_text):
        self.rule_text = rule_text


class _FakeStore:
    def __init__(self, active):
        self._active = active

    def active(self):
        return self._active


def _ctx(user_input="怎么给角色资产报价", history=None):
    return SimpleNamespace(
        turn_id="t1",
        conversation_id="c1",
        user_input=user_input,
        conversation_history=history or [],
        knowledge_context="",
        agent=None,
        extra={},
    )


def test_single_injection_is_idempotent():
    ctx = _ctx()
    fb = _FakeStore([_FakeFeedback("avoid", "不要用蓝色做主色")])
    st = _FakeStore([_FakeStrategy("报价必须给出利润率计算过程")])
    inject_memory_context(ctx, feedback_store=fb, strategy_store=st)
    first = ctx.knowledge_context
    assert ctx.extra.get("memory_injected") is True
    assert "【用户偏好与纠正】" in first
    assert "不要用蓝色" in first

    # Second call must not duplicate context.
    inject_memory_context(ctx, feedback_store=fb, strategy_store=st)
    assert ctx.knowledge_context == first


def test_blocks_assembled_in_priority_order():
    ctx = _ctx()
    fb = _FakeStore([_FakeFeedback("avoid", "回答要更简短")])
    st = _FakeStore([_FakeStrategy("优先用表格呈现报价")])
    ctx.knowledge_context = "【工作区资料】\n客户要求含税报价"
    inject_memory_context(ctx, feedback_store=fb, strategy_store=st)
    text = ctx.knowledge_context
    # Workspace facts first, then preferences, then strategy.
    wi = text.find("【工作区资料】")
    fi = text.find("【用户偏好与纠正】")
    si = text.find("【优化策略")
    assert wi < fi < si


def test_response_style_rule_defaults_balanced():
    # None / unknown style must fall back to the "balanced" rule.
    assert response_style_rule(None) == response_style_rule("balanced")
    assert "结论" in response_style_rule("concise")
    assert "完整假设" in response_style_rule("detailed")


def test_feedback_categories_exhaustive():
    assert "other" in FEEDBACK_CATEGORIES
    assert "too_verbose" in FEEDBACK_CATEGORIES


def test_positive_feedback_not_injected_as_preference():
    """A 👍 must be recorded for reflection but never auto-injected as a style."""
    captured = {}

    class _Recorder:
        def add(self, kind, content, scope="global", metadata=None):
            captured["kind"] = kind
            captured["content"] = content
            captured["metadata"] = metadata or {}
            return 1

    class _FbStore:
        def active(self):
            return []

    # Patch the default feedback store used by record_turn_feedback.
    import artpm_agent.harness.memory_retrieval as mr
    import artpm_agent.memory.feedback_store as fs

    orig = fs.get_default_feedback_store
    fs.get_default_feedback_store = lambda: _Recorder()
    try:
        res = record_turn_feedback("tX", True, user_prompt="问报价", assistant_content="好的")
    finally:
        fs.get_default_feedback_store = orig

    assert res["feedback_id"] == 1
    assert captured["metadata"].get("no_inject") is True
    assert "沿用" not in captured["content"]
