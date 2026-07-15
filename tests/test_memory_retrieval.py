"""Tests for the memory-retrieval hook (Phase 1 memory activation)."""
from types import SimpleNamespace

from artpm_agent.harness.memory_retrieval import (
    format_feedback_context,
    format_strategy_context,
    inject_memory_context,
    retrieve_memory_context,
)
from artpm_agent.memory.feedback_store import FeedbackStore
from artpm_agent.evolution.strategy_store import Strategy, StrategyStore


class FakeMemoryManager:
    def __init__(self, docs):
        self.docs = docs

    def retrieve(self, query, top_k=5, filters=None):
        return self.docs[:top_k]


class FakeKnowledgeStore:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return self.docs[: kwargs.get("limit", 4)]


def test_retrieve_from_memory_manager():
    mm = FakeMemoryManager([{"data": {"raw_text": "项目A毛利率30%"}}])
    out = retrieve_memory_context("项目A", [], memory_manager=mm)
    assert "相关记忆" in out
    assert "30%" in out


def test_retrieve_merges_memory_and_workspace_knowledge():
    mm = FakeMemoryManager([{"data": {"raw_text": "用户偏好：周报要短"}}])
    ks = FakeKnowledgeStore(
        [{"title": "报价模板", "text": "报价表必须包含风险说明", "confidence": 0.9}]
    )

    out = retrieve_memory_context("生成报价周报", [], memory_manager=mm, knowledge_store=ks)

    assert "长期记忆" in out
    assert "工作区资料" in out
    assert "周报要短" in out
    assert "风险说明" in out
    assert ks.calls[0]["use_confidence"] is True


def test_retrieve_filters_low_confidence_memory_to_reduce_pollution():
    mm = FakeMemoryManager(
        [
            {"data": {"raw_text": "低相关噪声"}, "metadata": {"confidence": 0.01}},
            {"data": {"raw_text": "高相关模板"}, "metadata": {"confidence": 0.91}},
        ]
    )
    ks = FakeKnowledgeStore(
        [
            {"title": "旧资料", "text": "不该注入", "confidence": 0.02},
            {"title": "新资料", "text": "应该注入", "confidence": 0.8},
        ]
    )

    out = retrieve_memory_context("模板", [], memory_manager=mm, knowledge_store=ks)

    assert "高相关模板" in out
    assert "应该注入" in out
    assert "低相关噪声" not in out
    assert "不该注入" not in out


def test_retrieve_empty_without_backend():
    assert retrieve_memory_context("x", [], memory_manager=None) == ""
    assert retrieve_memory_context("", [], memory_manager=FakeMemoryManager([{}])) == ""


def test_inject_appends_to_existing_knowledge_context():
    mm = FakeMemoryManager([{"data": {"raw_text": "mem fact"}}])
    ctx = SimpleNamespace(
        agent=SimpleNamespace(memory=mm),
        user_input="hi",
        conversation_history=[],
        knowledge_context="BASE",
        extra={},
    )
    inject_memory_context(ctx, memory_manager=mm)
    assert "BASE" in ctx.knowledge_context
    assert "mem fact" in ctx.knowledge_context
    assert ctx.extra.get("memory_injected") is True


def test_inject_with_feedback_and_strategy(tmp_path):
    fb = FeedbackStore(str(tmp_path / "fb.db"))
    fb.add("avoid", "别用 skill_x", scope="global")
    st = StrategyStore(str(tmp_path / "st.db"))
    st.add(Strategy(capability="global", rule_text="遇到模糊需求先澄清"))

    ctx = SimpleNamespace(
        agent=SimpleNamespace(memory=None),
        user_input="hi",
        conversation_history=[],
        knowledge_context="",
        extra={},
    )
    inject_memory_context(ctx, feedback_store=fb, strategy_store=st)
    assert "用户偏好" in ctx.knowledge_context
    assert "优化策略" in ctx.knowledge_context
    assert "别用 skill_x" in ctx.knowledge_context


def test_format_helpers():
    from artpm_agent.memory.feedback_store import FeedbackEntry

    fb_block = format_feedback_context(
        [FeedbackEntry(kind="avoid", content="x", scope="global")]
    )
    assert "用户偏好" in fb_block

    st_block = format_strategy_context(
        [Strategy(capability="global", rule_text="y")]
    )
    assert "优化策略" in st_block
