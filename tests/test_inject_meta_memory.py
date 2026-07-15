"""Integration test: Phase 4 meta-memory gap block is injected into context."""

import types

import pytest

from artpm_agent.harness import memory_retrieval


class _Ctx:
    def __init__(self, user_input):
        self.user_input = user_input
        self.conversation_history = []
        self.knowledge_context = ""
        self.extra = {}
        self.agent = types.SimpleNamespace(memory=None)


@pytest.fixture
def _patch_defaults(monkeypatch):
    # 用 None 替代默认 store，避免测试写真实 db；meta_store 也置空
    monkeypatch.setattr(
        memory_retrieval, "get_default_feedback_store", lambda: None
    )
    monkeypatch.setattr(
        memory_retrieval, "get_default_strategy_store", lambda: None
    )
    import artpm_agent.evolution.meta_memory as mm

    monkeypatch.setattr(mm, "get_default_meta_memory_store", lambda: None)


def test_meta_gap_block_injected_on_unknown_topic(_patch_defaults):
    ctx = _Ctx("帮我查一下 https://example.com 的接口文档")
    memory_retrieval.inject_memory_context(ctx, knowledge_store=None)
    assert "元记忆" in ctx.knowledge_context
    assert ctx.extra.get("meta_memory")
    assert ctx.extra["meta_memory"]["gaps"]


def test_no_gap_when_knowledge_present(_patch_defaults, monkeypatch):
    import artpm_agent.evolution.meta_memory as mm

    class _FakeKB:
        def search(self, query, limit=10, **kwargs):
            return [{"confidence": 0.95, "searchable_text": "高把握知识"}]

    monkeypatch.setattr(mm, "get_default_meta_memory", lambda: mm.MetaMemory())
    ctx = _Ctx("已知主题")
    memory_retrieval.inject_memory_context(ctx, knowledge_store=_FakeKB())
    # 已知主题不应产生缺口块
    assert "元记忆" not in ctx.knowledge_context
