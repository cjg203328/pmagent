from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from artpm_agent.harness.memory_retrieval import retrieve_memory_context
from artpm_agent.memory import (
    LegacyWorkspaceWriteRetiredError,
    MemoryManager as _MemoryManager,
)
from artpm_agent.request_orchestrator import RequestOrchestrator
from artpm_agent.tenancy import (
    TenantContext,
    TenantContextManager,
    WorkspaceAccessDenied,
)


def MemoryManager(*args, **kwargs):
    """Build the explicitly opted-in legacy fixture used by migration tests."""
    kwargs.setdefault("allow_legacy_workspace_writes", True)
    return _MemoryManager(*args, **kwargs)


def test_unbound_memory_manager_rejects_retired_workspace_writes(tmp_path):
    memory = _MemoryManager(str(tmp_path / "memory.db"), str(tmp_path / "vectors"))

    with pytest.raises(LegacyWorkspaceWriteRetiredError, match="retired"):
        memory.save_document({"raw_text": "must use canonical knowledge"})


def test_memory_manager_retrieval_is_scoped_to_workspace(tmp_path):
    memory = MemoryManager(str(tmp_path / "memory.db"), str(tmp_path / "vectors"))
    first_id = memory.save_document(
        {
            "workspace_id": "workspace-a",
            "document_type": "note",
            "raw_text": "SHARED-TERM workspace-a confidential budget",
        }
    )
    memory.save_document(
        {
            "workspace_id": "workspace-b",
            "document_type": "note",
            "raw_text": "SHARED-TERM workspace-b confidential budget",
        }
    )

    results = memory.retrieve(
        "SHARED-TERM", filters={"workspace_id": "workspace-a"}, top_k=5
    )
    context = retrieve_memory_context(
        "SHARED-TERM", memory_manager=memory, workspace_id="workspace-a"
    )

    assert [item["id"] for item in results] == [first_id]
    assert "workspace-a confidential" in context
    assert "workspace-b confidential" not in context


def test_memory_manager_rejects_empty_query_and_invalid_top_k(tmp_path):
    memory = MemoryManager(str(tmp_path / "memory.db"), str(tmp_path / "vectors"))

    for query in ("", "   ", None, 123):
        with pytest.raises(ValueError, match="query"):
            memory.retrieve(query, top_k=1)  # type: ignore[arg-type]

    for top_k in (0, -1, True, 1.5, "2", None):
        with pytest.raises(ValueError, match="top_k"):
            memory.retrieve("anything", top_k=top_k)  # type: ignore[arg-type]


def test_memory_manager_drops_malformed_vector_rows_before_hydration(tmp_path):
    memory = MemoryManager(str(tmp_path / "memory.db"), str(tmp_path / "vectors"))
    memory.save_document({"id": "valid", "raw_text": "valid memory"})

    class DirtyVectorStore:
        available = True

        def search(self, *_args, **_kwargs):
            return [
                {"id": "missing", "score": float("nan"), "metadata": {}},
                {"id": "missing", "score": 0.8, "metadata": []},
                {
                    "id": "valid",
                    "score": 0.7,
                    "metadata": {
                        "tenant_id": "local",
                        "workspace_id": "local-default",
                    },
                },
            ]

    memory.vector_db = DirtyVectorStore()
    results = memory.retrieve("valid", top_k=3)

    assert [item["id"] for item in results] == ["valid"]


def test_memory_manager_migrates_legacy_document_table_to_local_workspace(tmp_path):
    database_path = tmp_path / "legacy-memory.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                document_type TEXT,
                source TEXT,
                file_path TEXT,
                file_hash TEXT,
                extracted_data TEXT,
                raw_text TEXT,
                confidence REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO documents (id, raw_text) VALUES ('legacy-doc', 'legacy memory')"
        )

    memory = MemoryManager(str(database_path), str(tmp_path / "vectors"))

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    assert "workspace_id" in columns
    assert (
        memory.retrieve("legacy memory", filters={"workspace_id": "local-default"})[0][
            "id"
        ]
        == "legacy-doc"
    )


def test_memory_manager_uses_current_tenant_scope_and_rejects_conflicts(tmp_path):
    memory = MemoryManager(str(tmp_path / "memory.db"), str(tmp_path / "vectors"))
    memory.save_document(
        {
            "id": "doc-a",
            "workspace_id": "workspace-a",
            "document_type": "note",
            "raw_text": "tenant-a-only memory",
        }
    )
    memory.save_document(
        {
            "id": "doc-b",
            "workspace_id": "workspace-b",
            "document_type": "note",
            "raw_text": "tenant-b-only memory",
        }
    )
    context = TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )

    # Calls made outside a request retain the legacy local-default boundary;
    # they must not turn into an all-workspace search.
    assert memory.retrieve("only memory", top_k=5) == []

    with TenantContextManager.use(context):
        scoped = memory.retrieve("only memory", top_k=5)
        assert [item["id"] for item in scoped] == ["doc-a"]
        with pytest.raises(WorkspaceAccessDenied, match="workspace"):
            memory.retrieve(
                "only memory",
                filters={"workspace_id": "workspace-b"},
            )


def test_memory_manager_isolates_tenants_sharing_a_workspace(tmp_path):
    memory = MemoryManager(str(tmp_path / "memory.db"), str(tmp_path / "vectors"))
    memory.save_document(
        {
            "id": "tenant-a-doc",
            "tenant_id": "tenant-a",
            "workspace_id": "shared-workspace",
            "raw_text": "tenant-a private memory",
        }
    )
    memory.save_document(
        {
            "id": "tenant-b-doc",
            "tenant_id": "tenant-b",
            "workspace_id": "shared-workspace",
            "raw_text": "tenant-b private memory",
        }
    )

    tenant_a = TenantContext(tenant_id="tenant-a", workspace_id="shared-workspace")
    tenant_b = TenantContext(tenant_id="tenant-b", workspace_id="shared-workspace")

    assert [
        item["id"]
        for item in memory.retrieve("private memory", tenant_context=tenant_a)
    ] == ["tenant-a-doc"]
    assert [
        item["id"]
        for item in memory.retrieve("private memory", tenant_context=tenant_b)
    ] == ["tenant-b-doc"]


def test_feedback_write_preserves_workspace_scope(tmp_path):
    from artpm_agent.memory.feedback_store import FeedbackStore

    store = FeedbackStore(str(tmp_path / "feedback.db"))
    store.add(
        "preference",
        "报价默认带风险说明",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )
    store.add(
        "preference",
        "报价默认带交付日期",
        tenant_id="tenant-a",
        workspace_id="workspace-b",
        principal_id="user-a",
    )

    workspace_a = store.active(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )
    workspace_b = store.active(
        tenant_id="tenant-a",
        workspace_id="workspace-b",
        principal_id="user-a",
    )

    assert [entry.content for entry in workspace_a] == ["报价默认带风险说明"]
    assert [entry.content for entry in workspace_b] == ["报价默认带交付日期"]


def test_episode_store_filters_workspace_and_principal_scope(tmp_path):
    from artpm_agent.memory.episode_store import Episode, EpisodeStore

    store = EpisodeStore(str(tmp_path / "episodes.db"))
    store.record(
        Episode(
            turn_id="turn-a",
            conversation_id="conversation-a",
            handler="model",
            success=False,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_id="user-a",
        )
    )
    store.record(
        Episode(
            turn_id="turn-b",
            conversation_id="conversation-b",
            handler="model",
            success=True,
            tenant_id="tenant-a",
            workspace_id="workspace-b",
            principal_id="user-a",
        )
    )
    store.record(
        Episode(
            turn_id="turn-a-2",
            conversation_id="conversation-a",
            handler="model",
            success=True,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_id="user-b",
        )
    )

    assert [
        episode.turn_id
        for episode in store.recent(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_id="user-a",
        )
    ] == ["turn-a"]
    assert (
        store.count(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            all_principals=True,
        )
        == 2
    )
    assert store.failure_rate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        all_principals=True,
    ) == pytest.approx(0.5)


def test_episode_store_feedback_update_respects_scope(tmp_path):
    from artpm_agent.memory.episode_store import Episode, EpisodeStore

    store = EpisodeStore(str(tmp_path / "episodes.db"))
    for workspace_id in ("workspace-a", "workspace-b"):
        store.record(
            Episode(
                turn_id="same-turn-id",
                conversation_id=workspace_id,
                handler="model",
                success=True,
                tenant_id="tenant-a",
                workspace_id=workspace_id,
                principal_id="user-a",
            )
        )

    assert (
        store.set_feedback(
            "same-turn-id",
            "workspace-a feedback",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_id="user-a",
        )
        == 1
    )
    assert (
        store.recent_feedback(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_id="user-a",
        )[0].feedback
        == "workspace-a feedback"
    )
    assert (
        store.recent_feedback(
            tenant_id="tenant-a",
            workspace_id="workspace-b",
            principal_id="user-a",
        )
        == []
    )


def test_memory_manager_rejects_explicit_context_conflicting_with_current(tmp_path):
    memory = MemoryManager(str(tmp_path / "memory.db"), str(tmp_path / "vectors"))
    current = TenantContext(tenant_id="tenant-a", workspace_id="workspace-a")
    explicit = TenantContext(tenant_id="tenant-b", workspace_id="workspace-b")

    with TenantContextManager.use(current):
        with pytest.raises(WorkspaceAccessDenied, match="contexts conflict"):
            memory.retrieve("anything", tenant_context=explicit)


def test_query_memory_forwards_trusted_tenant_scope(tmp_path):
    memory = MemoryManager(str(tmp_path / "memory.db"), str(tmp_path / "vectors"))
    memory.save_document(
        {
            "id": "doc-a",
            "workspace_id": "workspace-a",
            "raw_text": "scoped query memory",
        }
    )
    memory.save_document(
        {
            "id": "doc-b",
            "workspace_id": "workspace-b",
            "raw_text": "scoped query memory",
        }
    )
    orchestrator = object.__new__(RequestOrchestrator)
    orchestrator.memory = memory
    orchestrator.config = SimpleNamespace(get=lambda _key, default=5: default)
    context = TenantContext(tenant_id="tenant-a", workspace_id="workspace-a")

    result = orchestrator.query_memory("scoped query", tenant_context=context)

    assert result["success"] is True
    assert [item["id"] for item in result["results"]] == ["doc-a"]

    conflicted = orchestrator.query_memory(
        "scoped query",
        workspace_id="workspace-b",
        context={"tenant_context": context},
    )
    assert conflicted["success"] is False
    assert "workspace" in conflicted["error"]
