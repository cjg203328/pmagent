from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "artpm_agent"

from artpm_agent.memory import (
    DeterministicEmbeddingProvider,
    MemoryManager,
    VectorStore,
    WorkspaceKnowledgeStore,
)


class SemanticTestEmbedding:
    dimension = 4
    fingerprint = "test-semantic-v1:4"

    def __init__(self):
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(str(text))
        value = str(text).casefold()
        if any(word in value for word in ("预算", "成本", "费用", "超支", "报价")):
            return [1.0, 0.0, 0.0, 0.0]
        if any(word in value for word in ("色彩", "颜色", "饱和度")):
            return [0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 1.0, 0.0]


class ConstantEmbedding:
    dimension = 4
    fingerprint = "test-constant-v1:4"

    def embed(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]


class FailingEmbedding(ConstantEmbedding):
    fingerprint = "test-failing-v1:4"

    def embed(self, _text: str) -> list[float]:
        raise RuntimeError("embedding unavailable")


def test_vector_store_persists_upserts_and_metadata_filters(tmp_path):
    provider = DeterministicEmbeddingProvider(dimension=64)
    path = tmp_path / "中文 vector store"
    store = VectorStore(
        path,
        dimension=provider.dimension,
        embedding_fingerprint=provider.fingerprint,
    )
    store.add(
        "doc-a",
        provider.embed("角色报价预算"),
        {"workspace_id": "studio-a"},
    )
    store.add(
        "doc-b",
        provider.embed("色彩规范"),
        {"workspace_id": "studio-b"},
    )

    assert store.count == 2
    assert store.search(
        provider.embed("角色报价预算"),
        filters={"workspace_id": "studio-a"},
    )[0]["id"] == "doc-a"

    store.upsert(
        id="doc-a",
        vector=provider.embed("角色成本预算"),
        metadata={"workspace_id": "studio-a", "version": 2},
    )
    assert store.count == 2
    assert next(
        item for item in store.list_entries() if item["id"] == "doc-a"
    )["metadata"]["version"] == 2

    reopened = VectorStore(
        path,
        dimension=provider.dimension,
        embedding_fingerprint=provider.fingerprint,
    )
    assert reopened.count == 2
    assert reopened.status()["needs_rebuild"] is False
    assert reopened.search(provider.embed("角色成本预算"))[0]["id"] == "doc-a"


def test_vector_store_reuses_snapshot_and_detects_external_updates(tmp_path):
    provider = DeterministicEmbeddingProvider(dimension=64)
    path = tmp_path / "vectors"
    store = VectorStore(
        path,
        dimension=provider.dimension,
        embedding_fingerprint=provider.fingerprint,
    )
    store.add("doc-a", provider.embed("first"), {"workspace_id": "studio-a"})

    # A read on the same instance should keep the already-loaded FAISS snapshot.
    generation = store._backend._generation
    assert store.search(provider.embed("first"))[0]["id"] == "doc-a"
    assert store._backend._generation == generation

    # A separate writer atomically replaces the files; the first instance must
    # notice the changed stat signature before serving its next search.
    writer = VectorStore(
        path,
        dimension=provider.dimension,
        embedding_fingerprint=provider.fingerprint,
    )
    writer.add("doc-b", provider.embed("second"), {"workspace_id": "studio-a"})
    results = store.search(provider.embed("second"), top_k=2)
    assert {item["id"] for item in results} == {"doc-a", "doc-b"}


def test_vector_store_recovers_from_incompatible_and_corrupt_files(tmp_path):
    path = tmp_path / "vectors"
    first = DeterministicEmbeddingProvider(dimension=64)
    store = VectorStore(
        path,
        dimension=first.dimension,
        embedding_fingerprint=first.fingerprint,
    )
    store.add("doc-a", first.embed("报价"), {})

    changed = DeterministicEmbeddingProvider(dimension=128)
    incompatible = VectorStore(
        path,
        dimension=changed.dimension,
        embedding_fingerprint=changed.fingerprint,
    )
    assert incompatible.available is True
    assert incompatible.needs_rebuild is True
    assert incompatible.count == 0
    assert list(path.glob("*.incompatible-*"))

    incompatible.replace(
        [{"id": "doc-b", "vector": changed.embed("预算"), "metadata": {}}]
    )
    (path / "metadata.json").write_text("not-json", encoding="utf-8")
    recovered = VectorStore(
        path,
        dimension=changed.dimension,
        embedding_fingerprint=changed.fingerprint,
    )
    assert recovered.needs_rebuild is True
    assert recovered.count == 0


def test_workspace_knowledge_uses_vector_retrieval_and_persists(tmp_path):
    database_path = tmp_path / "knowledge.db"
    vector_path = tmp_path / "knowledge-vectors"
    provider = SemanticTestEmbedding()
    store = WorkspaceKnowledgeStore(
        database_path,
        vector_store_path=vector_path,
        embedding_provider=provider,
    )
    budget = store.ingest_resource(
        title="项目财务复盘",
        searchable_text="制作费用已经超过原定计划，需要重新评估报价。",
        resource_type="document",
        source_type="manual",
    )
    store.ingest_resource(
        title="视觉规范",
        searchable_text="主视觉需要降低饱和度。",
        resource_type="document",
        source_type="manual",
    )

    # The query shares no full literal phrase with the document; the embedding
    # provider intentionally maps both to the same semantic direction.
    provider.calls.clear()
    results = store.search("角色成本超支怎么办", include_rules=False)
    assert results[0]["id"] == budget["id"]
    assert results[0]["retrieval_mode"] == "vector"
    assert provider.calls == ["角色成本超支怎么办"]
    assert store.vector_status()["count"] == 2
    assert store.vector_status()["last_search_mode"] == "vector"

    reopened = WorkspaceKnowledgeStore(
        database_path,
        vector_store_path=vector_path,
        embedding_provider=provider,
    )
    assert reopened.search("预算风险", include_rules=False)[0]["id"] == budget["id"]
    assert reopened.vector_status()["needs_rebuild"] is False


def test_workspace_search_does_not_run_full_vector_sync(tmp_path, monkeypatch):
    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        vector_store_path=tmp_path / "vectors",
        embedding_provider=SemanticTestEmbedding(),
    )
    resource = store.ingest_resource(
        title="Search without reconciliation",
        searchable_text="budget cost review",
    )

    def fail_full_sync(*_args, **_kwargs):
        raise AssertionError("search must not run a full vector reconciliation")

    monkeypatch.setattr(store, "_sync_vector_index", fail_full_sync)
    result = store.search("budget", include_rules=False)

    assert result[0]["id"] == resource["id"]


def test_workspace_vector_index_tracks_archive_and_provider_changes(tmp_path):
    database_path = tmp_path / "knowledge.db"
    vector_path = tmp_path / "knowledge-vectors"
    provider = SemanticTestEmbedding()
    store = WorkspaceKnowledgeStore(
        database_path,
        vector_store_path=vector_path,
        embedding_provider=provider,
    )
    resource = store.ingest_resource(
        title="预算说明",
        searchable_text="角色制作成本需要预留返修费用。",
        resource_type="document",
        source_type="manual",
    )
    assert store.vector_status()["count"] == 1
    assert store.archive_resource(resource["id"]) is True
    assert store.vector_status()["count"] == 0
    assert store.search("成本超支", include_rules=False) == []

    restored = store.ingest_resource(
        title="预算说明",
        searchable_text="角色制作成本需要预留返修费用。",
        resource_type="document",
        source_type="manual",
        resource_id=resource["id"],
    )
    assert restored["id"] == resource["id"]
    assert store.vector_status()["count"] == 1

    changed_provider = DeterministicEmbeddingProvider(dimension=64)
    changed = WorkspaceKnowledgeStore(
        database_path,
        vector_store_path=vector_path,
        embedding_provider=changed_provider,
    )
    assert changed.rebuild_vector_index()["dimension"] == 64
    assert changed.vector_status()["count"] == 1


def test_workspace_search_reports_literal_fallback_when_vectors_are_disabled(tmp_path):
    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        enable_vector_search=False,
    )
    resource = store.ingest_resource(
        title="交付规范",
        searchable_text="角色贴图统一使用 TGA 格式",
    )

    result = store.search("TGA 格式", include_rules=False)[0]
    assert result["id"] == resource["id"]
    assert result["retrieval_mode"] == "literal-fallback"
    assert store.vector_status()["available"] is False


def test_memory_manager_rebuilds_missing_index_from_sqlite(tmp_path):
    database_path = tmp_path / "memory.db"
    vector_path = tmp_path / "memory-vectors"
    memory = MemoryManager(str(database_path), str(vector_path))
    document_id = memory.save_document(
        {
            "document_type": "报价单",
            "raw_text": "角色项目报价和制作预算",
            "extracted_data": {
                "project_info": {"project_name": "角色项目"}
            },
        }
    )
    assert memory.vector_db.count == 1
    memory.vector_db.clear()
    assert memory.vector_db.count == 0

    reopened = MemoryManager(str(database_path), str(vector_path))
    assert reopened.vector_db.count == 1
    assert reopened.retrieve("角色项目报价")[0]["id"] == document_id


def test_memory_manager_batches_vector_document_hydration(tmp_path, monkeypatch):
    memory = MemoryManager(
        str(tmp_path / "memory.db"),
        str(tmp_path / "memory-vectors"),
    )
    first = memory.save_document({"id": "doc-a", "raw_text": "alpha budget"})
    second = memory.save_document({"id": "doc-b", "raw_text": "alpha delivery"})
    calls = {"batch": 0, "single": 0}
    original_batch = memory.db.get_by_ids

    def tracked_batch(table, ids):
        calls["batch"] += 1
        return original_batch(table, ids)

    def forbidden_single(*_args, **_kwargs):
        calls["single"] += 1
        raise AssertionError("vector hydration must not issue N+1 get_by_id calls")

    monkeypatch.setattr(memory.db, "get_by_ids", tracked_batch)
    monkeypatch.setattr(memory.db, "get_by_id", forbidden_single)

    results = memory.retrieve("alpha", top_k=2)

    assert {item["id"] for item in results} == {first, second}
    assert calls == {"batch": 1, "single": 0}


def test_multiple_memory_managers_do_not_overwrite_each_others_vectors(tmp_path):
    database_path = tmp_path / "memory.db"
    vector_path = tmp_path / "memory-vectors"
    first = MemoryManager(str(database_path), str(vector_path))
    second = MemoryManager(str(database_path), str(vector_path))

    first.save_document({"id": "doc-a", "raw_text": "alpha"})
    second.save_document({"id": "doc-b", "raw_text": "beta"})

    reopened = MemoryManager(str(database_path), str(vector_path))
    assert {item["id"] for item in reopened.vector_db.list_entries()} == {
        "doc-a",
        "doc-b",
    }


def test_committed_knowledge_survives_vector_sync_failure(tmp_path):
    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        vector_store_path=tmp_path / "vectors",
        embedding_provider=FailingEmbedding(),
    )

    resource = store.ingest_resource(
        resource_id="resource-a",
        title="Committed resource",
        searchable_text="authoritative database content",
    )

    assert resource["id"] == "resource-a"
    assert store.get_resource("resource-a")["title"] == "Committed resource"
    assert store.vector_status()["needs_rebuild"] is True
    assert "sync pending" in store.vector_status()["last_error"]


def test_vector_candidates_expand_until_distinct_resources_fill_limit(tmp_path):
    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        vector_store_path=tmp_path / "vectors",
        embedding_provider=ConstantEmbedding(),
    )
    store.ingest_resource(
        resource_id="long",
        title="Long resource",
        searchable_text="long unrelated body. " * 6000,
    )
    for index in range(5):
        store.ingest_resource(
            resource_id=f"short-{index}",
            title=f"Short {index}",
            searchable_text=f"short unrelated body {index}",
        )

    results = store.search("semantic-only-query", limit=3, include_rules=False)

    assert len(results) == 3
    assert len({item["id"] for item in results}) == 3


def test_chat_knowledge_context_uses_vector_results(tmp_path, monkeypatch):
    from artpm_agent import ui_helpers

    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        vector_store_path=tmp_path / "knowledge-vectors",
        embedding_provider=SemanticTestEmbedding(),
    )
    store.ingest_resource(
        title="项目风险说明",
        searchable_text="制作费用已经超过计划，需要重新评估报价。",
    )
    monkeypatch.setattr(ui_helpers, "get_knowledge_store", lambda: store)

    context = ui_helpers.build_knowledge_context("角色成本超支怎么办")

    assert "相关资料" in context
    assert "项目风险说明" in context
    assert store.last_search_mode == "vector"
