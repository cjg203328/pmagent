"""Tests for Phase 4 MetaMemory (known / unknown / gap analysis)."""

from pathlib import Path

import pytest

from artpm_agent.evolution.meta_memory import (
    MetaMemory,
    MetaMemoryStore,
    format_meta_memory_context,
)
from artpm_agent.tenancy import TenantContext, TenantContextManager, WorkspaceAccessDenied


class _FakeKnowledgeStore:
    """最小知识库桩：按给定置信度返回检索命中。"""

    def __init__(self, hits=None):
        self._hits = hits if hits is not None else []

    def search(self, query, limit=10, **kwargs):
        return self._hits


class _FakeStrategyStore:
    def __init__(self, active=None):
        self._active = active if active is not None else []

    def active(self, capability=None):
        return self._active


def test_unknown_external_topic_suggests_search():
    mm = MetaMemory()
    report = mm.analyze(
        "请联网查一下 https://example.com 的 API 文档",
        retrieved_block="",
        knowledge_store=_FakeKnowledgeStore([]),
    )
    assert report.has_gaps()
    assert report.unknown_topics
    gap = report.gaps[0]
    assert gap.kind == "unknown"
    assert gap.suggested_action == "search"
    block = format_meta_memory_context(report)
    assert "联网检索" in block


def test_unknown_internal_topic_suggests_ask_user():
    mm = MetaMemory()
    report = mm.analyze(
        "帮我整理这个项目的交付流程",
        retrieved_block="",
        knowledge_store=_FakeKnowledgeStore([]),
    )
    assert report.has_gaps()
    assert report.gaps[0].suggested_action == "ask_user"
    assert "向用户澄清" in format_meta_memory_context(report)


def test_low_confidence_knowledge_flags_gap():
    mm = MetaMemory(confidence_threshold=0.5)
    hits = [{"confidence": 0.3, "searchable_text": "相关但不确定"}]
    report = mm.analyze(
        "某主题",
        retrieved_block="【相关记忆】相关但不确定",
        knowledge_store=_FakeKnowledgeStore(hits),
    )
    assert any(g.kind == "low_confidence" for g in report.gaps)


def test_known_topic_has_no_gap():
    mm = MetaMemory(confidence_threshold=0.5)
    hits = [{"confidence": 0.9, "searchable_text": "高把握知识"}]
    report = mm.analyze(
        "某主题",
        retrieved_block="【相关记忆】高把握知识",
        knowledge_store=_FakeKnowledgeStore(hits),
    )
    assert not report.has_gaps()
    assert report.known_topics


def test_high_confidence_hit_counts_as_known_without_rendered_block():
    mm = MetaMemory(confidence_threshold=0.5)
    hits = [{"confidence": 0.95, "searchable_text": "高把握知识"}]
    report = mm.analyze(
        "某主题",
        retrieved_block="",
        knowledge_store=_FakeKnowledgeStore(hits),
    )

    assert report.known_topics == ["某主题"]
    assert not report.has_gaps()


def test_no_strategy_store_triggers_gap():
    mm = MetaMemory(confidence_threshold=0.5)
    # 置信度低于阈值 → 不会走「已知」早返回，从而能继续判定无策略缺口
    hits = [{"confidence": 0.4, "searchable_text": "低把握知识"}]
    report = mm.analyze(
        "某主题",
        retrieved_block="【相关记忆】低把握知识",
        knowledge_store=_FakeKnowledgeStore(hits),
        strategy_store=_FakeStrategyStore([]),  # 无已采纳策略
    )
    assert any(g.kind == "no_strategy" for g in report.gaps)


def test_meta_store_persists_and_counts_gaps(tmp_path: Path):
    store = MetaMemoryStore(tmp_path / "meta.db")
    from artpm_agent.evolution.meta_memory import KnowledgeGap

    gaps = [
        KnowledgeGap("主题X", "unknown", "ask_user", "缺口详情", 0.0),
    ]
    store.record_gaps(gaps)
    store.record_gaps(gaps)  # 第二次出现 → seen_count 累加
    top = store.top_gaps()
    assert len(top) == 1
    assert top[0]["seen_count"] == 2
    assert top[0]["topic"] == "主题X"


def test_meta_store_isolates_same_topic_by_tenant_workspace_and_principal(
    tmp_path: Path,
):
    store = MetaMemoryStore(tmp_path / "meta-scoped.db")
    from artpm_agent.evolution.meta_memory import KnowledgeGap

    gap = KnowledgeGap("shared-topic", "unknown", "ask_user", "missing", 0.0)
    store.record_gaps(
        [gap],
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )
    store.record_gaps(
        [gap],
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-b",
    )
    store.record_gaps(
        [gap],
        tenant_id="tenant-b",
        workspace_id="workspace-b",
        principal_id="user-a",
    )

    user_a = store.top_gaps(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )
    user_b = store.top_gaps(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-b",
    )
    other_tenant = store.top_gaps(
        tenant_id="tenant-b",
        workspace_id="workspace-b",
        principal_id="user-a",
    )

    assert user_a[0]["seen_count"] == 1
    assert user_b[0]["seen_count"] == 1
    assert other_tenant[0]["seen_count"] == 1

    with TenantContextManager.use(
        TenantContext(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_id="user-a",
        )
    ):
        with pytest.raises(WorkspaceAccessDenied):
            store.top_gaps(
                tenant_id="tenant-b",
                workspace_id="workspace-b",
                principal_id="user-a",
            )


def test_empty_input_returns_empty_report():
    mm = MetaMemory()
    report = mm.analyze("", knowledge_store=_FakeKnowledgeStore([]))
    assert not report.has_gaps()
