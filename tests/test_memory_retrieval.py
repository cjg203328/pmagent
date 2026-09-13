"""Tests for the memory-retrieval hook (Phase 1 memory activation)."""
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from artpm_agent.harness.memory_retrieval import (
    format_feedback_context,
    format_strategy_context,
    inject_memory_context,
    retrieve_memory_context,
)
from artpm_agent.memory.feedback_store import FeedbackStore
from artpm_agent.evolution.strategy_store import Strategy, StrategyStore


def test_memory_injector_initializes_extraction_cursor(tmp_path):
    from artpm_agent.memory.memory_injector import MemoryInjector

    class _CrossMemory:
        def __init__(self):
            self.calls = 0
            self.extract_kwargs = []
            self.build_kwargs = []

        def extract_and_save_from_messages(self, messages, **kwargs):
            self.calls += 1
            self.extract_kwargs.append(kwargs)
            return []

        def build_memory_context(self, *args, **kwargs):
            self.build_kwargs.append(kwargs)
            return ""

    cross = _CrossMemory()
    injector = MemoryInjector(
        object(), cross_memory=cross, auto_compress=False
    )
    messages = [{"role": "user", "content": f"事实{i}"} for i in range(6)]
    injector.prepare_context(
        messages=messages,
        user_input="当前问题",
        conversation_id="c1",
        workspace_id="studio-b",
        llm_callable=lambda _: "[]",
    )
    injector.prepare_context(
        messages=messages,
        user_input="当前问题",
        conversation_id="c1",
        workspace_id="studio-b",
        llm_callable=lambda _: "[]",
    )
    assert cross.calls == 1
    assert cross.extract_kwargs[0]["workspace_id"] == "studio-b"
    assert all(item["workspace_id"] == "studio-b" for item in cross.build_kwargs)


class FakeMemoryManager:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def retrieve(self, query, top_k=5, filters=None):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
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


def test_retrieval_marks_legacy_evidence_as_pending_with_provenance():
    mm = FakeMemoryManager(
        [
            {
                "data": {"raw_text": "旧系统返回的待核实内容"},
                "metadata": {"source_type": "legacy", "source_id": "m-1"},
            }
        ]
    )

    out = retrieve_memory_context("待核实", memory_manager=mm)

    assert "[待确认]" in out
    assert "来源:legacy/m-1" in out
    assert "旧系统返回的待核实内容" in out


def test_retrieval_skips_unapproved_and_expired_evidence():
    expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    ks = FakeKnowledgeStore(
        [
            {
                "title": "模型提案",
                "text": "不应注入的未批准内容",
                "status": "proposed",
                "confidence": 0.9,
            },
            {
                "title": "过期资料",
                "text": "不应注入的过期内容",
                "status": "active",
                "expires_at": expires_at,
                "confidence": 0.9,
            },
            {
                "title": "正式资料",
                "text": "应注入的正式内容",
                "status": "active",
                "source_type": "wiki",
                "source_id": "wiki-1",
                "current_version": 3,
                "updated_at": "2026-09-13T00:00:00+00:00",
                "confidence": 0.9,
            },
        ]
    )

    out = retrieve_memory_context("内容", knowledge_store=ks)

    assert "不应注入的未批准内容" not in out
    assert "不应注入的过期内容" not in out
    assert "应注入的正式内容" in out
    assert "来源:wiki/wiki-1" in out
    assert "版本:3" in out


def test_retrieval_query_uses_user_history_but_not_assistant_guesses():
    mm = FakeMemoryManager([{"data": {"raw_text": "相关事实"}}])
    history = [
        {"role": "user", "content": "星河项目预算"},
        {"role": "assistant", "content": "未经确认的错误项目名"},
    ]

    retrieve_memory_context("利润率是多少", history, memory_manager=mm)

    query = mm.calls[0]["query"]
    assert "星河项目预算" in query
    assert "未经确认的错误项目名" not in query


def test_retrieve_scopes_workspace_knowledge_search():
    ks = FakeKnowledgeStore(
        [{"title": "项目事实", "text": "Studio B 的预算是 30 万"}]
    )

    out = retrieve_memory_context(
        "预算",
        knowledge_store=ks,
        workspace_id="studio-b",
    )

    assert "30 万" in out
    assert ks.calls[0]["workspace_id"] == "studio-b"


def test_legacy_knowledge_backend_fails_closed_for_non_default_workspace():
    class LegacyStore:
        DEFAULT_WORKSPACE_ID = "local-default"

        def search(self, query, limit):
            return [{"text": "默认工作区的私有事实"}]

    out = retrieve_memory_context(
        "事实",
        knowledge_store=LegacyStore(),
        workspace_id="studio-b",
    )

    assert out == ""


def test_dedup_does_not_merge_documents_with_the_same_long_prefix():
    prefix = "相同模板前缀" * 45
    mm = FakeMemoryManager(
        [
            {"data": {"raw_text": prefix + "甲项目预算 10 万"}},
            {"data": {"raw_text": prefix + "乙项目预算 20 万"}},
        ]
    )

    out = retrieve_memory_context(
        "项目预算",
        memory_manager=mm,
        max_total_chars=2000,
    )

    assert "甲项目预算 10 万" in out
    assert "乙项目预算 20 万" in out


def test_retrieval_budget_keeps_evidence_from_each_backend():
    mm = FakeMemoryManager(
        [
            {"data": {"raw_text": f"长期记忆 {index} " + "甲" * 650}}
            for index in range(4)
        ]
    )
    ks = FakeKnowledgeStore(
        [{"title": "权威资料", "text": "工作区确认事实 " + "乙" * 650}]
    )

    out = retrieve_memory_context(
        "预算",
        memory_manager=mm,
        knowledge_store=ks,
        max_total_chars=900,
    )

    assert "长期记忆 0" in out
    assert "工作区确认事实" in out
    assert len(out) <= 900


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


def test_inject_propagates_workspace_scope_to_retrieval():
    ks = FakeKnowledgeStore([{"text": "工作区 B 的事实"}])
    empty_store = type("EmptyStore", (), {"active": lambda self: []})()
    ctx = SimpleNamespace(
        agent=None,
        conversation_id="conversation-b",
        user_input="事实",
        conversation_history=[],
        knowledge_context="",
        extra={"workspace_id": "studio-b"},
    )

    inject_memory_context(
        ctx,
        knowledge_store=ks,
        feedback_store=empty_store,
        strategy_store=empty_store,
    )

    assert any(call.get("workspace_id") == "studio-b" for call in ks.calls)


def test_inject_includes_accepted_workspace_rules():
    class RuleStore:
        DEFAULT_WORKSPACE_ID = "local-default"

        def get_active_rules(self, *, workspace_id, limit):
            assert workspace_id == "studio-b"
            return [{"statement": "交付物必须带版本号"}][:limit]

        def search(self, *_args, **_kwargs):
            return []

    empty_store = type("EmptyStore", (), {"active": lambda self: []})()
    ctx = SimpleNamespace(
        agent=None,
        conversation_id="conversation-rules",
        user_input="交付物有什么规则",
        conversation_history=[],
        knowledge_context="",
        extra={"workspace_id": "studio-b"},
    )

    inject_memory_context(
        ctx,
        knowledge_store=RuleStore(),
        feedback_store=empty_store,
        strategy_store=empty_store,
    )

    assert "交付物必须带版本号" in ctx.knowledge_context


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


def test_cross_session_memory_uses_content_level_identity(tmp_path):
    from artpm_agent.memory.cross_session_memory import CrossSessionMemory
    from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore

    store = WorkspaceKnowledgeStore(tmp_path / "kb.db", enable_vector_search=False)
    memory = CrossSessionMemory(store)
    first = memory.save_memory(
        "项目代号是星河计划", "fact", source_conversation="conversation-a"
    )
    second = memory.save_memory(
        "交付物必须带版本号", "fact", source_conversation="conversation-a"
    )
    preference = memory.save_memory(
        "周报默认使用中文", "user_preference", source_conversation="conversation-a"
    )

    assert first and second and preference
    assert len(store.list_resources()) == 3


def test_cross_session_memory_isolates_workspaces_and_excludes_current_chat(tmp_path):
    from artpm_agent.memory.cross_session_memory import CrossSessionMemory
    from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore

    store = WorkspaceKnowledgeStore(tmp_path / "kb.db", enable_vector_search=False)
    memory = CrossSessionMemory(store)
    memory.save_memory(
        "共享关键词：A 工作区预算 10 万",
        "fact",
        workspace_id="studio-a",
        source_conversation="conversation-a-current",
    )
    memory.save_memory(
        "共享关键词：A 工作区预算 20 万",
        "fact",
        workspace_id="studio-a",
        source_conversation="conversation-a-other",
    )
    memory.save_memory(
        "共享关键词：B 工作区预算 99 万",
        "fact",
        workspace_id="studio-b",
        source_conversation="conversation-b",
    )

    results = memory.retrieve(
        "共享关键词",
        workspace_id="studio-a",
        exclude_conversation="conversation-a-current",
    )

    assert [item.source_conversation for item in results] == [
        "conversation-a-other"
    ]
    assert "20 万" in results[0].content
    assert all("99 万" not in item.content for item in results)


def test_cross_session_memory_survives_store_reopen_and_model_replacement(tmp_path):
    from artpm_agent.memory.cross_session_memory import CrossSessionMemory
    from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore

    db_path = tmp_path / "global-memory.db"
    first_store = WorkspaceKnowledgeStore(db_path, enable_vector_search=False)
    CrossSessionMemory(first_store).save_memory(
        "全局规则：所有交付物必须包含版本号",
        "fact",
        workspace_id="global-workspace",
        source_conversation="conversation-one",
    )

    # A new store/manager represents a new process or a replaced LLM. The
    # durable knowledge database, not the model instance, is the source of truth.
    second_store = WorkspaceKnowledgeStore(db_path, enable_vector_search=False)
    results = CrossSessionMemory(second_store).retrieve(
        "交付物 版本号",
        workspace_id="global-workspace",
        top_k=3,
    )

    assert len(results) == 1
    assert "必须包含版本号" in results[0].content


def test_excluding_current_chat_fetches_extra_candidates_before_top_k(tmp_path):
    from artpm_agent.memory.cross_session_memory import CrossSessionMemory
    from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore

    store = WorkspaceKnowledgeStore(tmp_path / "kb.db", enable_vector_search=False)
    memory = CrossSessionMemory(store)
    memory.save_memory(
        "预算关键词：另一会话的有效事实",
        "fact",
        source_conversation="other-chat",
    )
    for index in range(5):
        memory.save_memory(
            f"预算关键词：当前会话事实 {index}",
            "fact",
            source_conversation="current-chat",
        )

    results = memory.retrieve(
        "预算关键词",
        top_k=2,
        exclude_conversation="current-chat",
    )

    assert [item.source_conversation for item in results] == ["other-chat"]


def test_conversation_compression_persists_to_the_selected_workspace():
    from artpm_agent.memory.conversation_compressor import ConversationCompressor

    class RecordingStore:
        def __init__(self):
            self.calls = []

        def ingest_resource(self, **kwargs):
            self.calls.append(kwargs)
            return {"id": str(len(self.calls))}

    store = RecordingStore()
    summary = (
        '{"summary":"项目预算已确认",'
        '"key_points":[],"user_preferences":["使用中文"],"entities":[]}'
    )

    result = ConversationCompressor().compress(
        [{"role": "user", "content": "预算是多少"}],
        lambda _prompt: summary,
        conversation_id="conversation-b",
        workspace_id="studio-b",
        knowledge_store=store,
    )

    assert result.was_compressed is True
    assert len(store.calls) == 2
    assert all(call["workspace_id"] == "studio-b" for call in store.calls)
