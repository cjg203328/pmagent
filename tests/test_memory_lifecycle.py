from __future__ import annotations

import pytest

from artpm_agent.evolution.meta_memory import MetaMemoryStore
from artpm_agent.evolution.strategy_store import Strategy, StrategyStore
from artpm_agent.memory.episode_store import Episode, EpisodeStore
from artpm_agent.memory.feedback_store import FeedbackStore
from artpm_agent.runtime.storage_registry import StorageRegistry


def _registry(tmp_path):
    return StorageRegistry(
        db_path=tmp_path / "state.db",
        vector_store_path=tmp_path / "vectors",
    )


def _seed_workspace(registry, *, tenant_id="tenant-a", workspace_id="workspace-a"):
    conversation = registry.conversation
    conversation.create_workspace(
        workspace_id,
        "Workspace A",
        tenant_id=tenant_id,
    )
    thread = conversation.create_conversation(
        "Export me",
        workspace_id=workspace_id,
    )
    conversation.add_message(
        thread["id"],
        "user",
        "private message",
        workspace_id=workspace_id,
    )
    registry.knowledge.ingest_resource(
        resource_id=f"resource-{workspace_id}",
        title="Private knowledge",
        searchable_text="private knowledge body",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    return thread


def test_lifecycle_exports_scoped_conversation_and_knowledge(tmp_path):
    registry = _registry(tmp_path)
    _seed_workspace(registry)

    exported = registry.lifecycle.export_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )

    assert exported["tenant_id"] == "tenant-a"
    assert exported["messages"][0]["content"] == "private message"
    assert exported["knowledge"]["resources"][0]["id"] == "resource-workspace-a"
    assert exported["retention_policy"]["authoritative_knowledge_days"] is None


def test_workspace_delete_requires_exact_confirmation_and_cleans_vectors(tmp_path):
    registry = _registry(tmp_path)
    _seed_workspace(registry)

    with pytest.raises(ValueError, match="confirmation"):
        registry.lifecycle.delete_workspace(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            confirmation="workspace-a",
        )

    report = registry.lifecycle.delete_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        confirmation="tenant-a:workspace-a",
    )

    assert report["status"] == "complete"
    assert report["resources"] == 1
    assert (
        registry.conversation.list_workspaces(tenant_id="tenant-a", profile_id=None)
        == []
    )
    assert registry.knowledge.vector_status()["count"] == 0
    with registry.knowledge._connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_index_outbox WHERE tenant_id = 'tenant-a'"
            ).fetchone()[0]
            == 0
        )


def test_tenant_offboarding_deletes_all_owned_workspaces(tmp_path):
    registry = _registry(tmp_path)
    _seed_workspace(registry, workspace_id="workspace-a")
    _seed_workspace(registry, workspace_id="workspace-b")

    report = registry.lifecycle.offboard_tenant(
        tenant_id="tenant-a",
        confirmation="tenant-a",
    )

    assert report["status"] == "complete"
    assert report["workspace_count"] == 2
    assert (
        registry.conversation.list_workspaces(tenant_id="tenant-a", profile_id=None)
        == []
    )


def test_workspace_delete_purges_bound_learning_memory(tmp_path):
    registry = _registry(tmp_path)
    _seed_workspace(registry)
    episodes = EpisodeStore(str(tmp_path / "episodes.db"))
    feedback = FeedbackStore(str(tmp_path / "feedback.db"))
    strategies = StrategyStore(str(tmp_path / "strategies.db"))
    meta = MetaMemoryStore(tmp_path / "meta.db")
    episodes.record(
        Episode(
            turn_id="turn-a",
            conversation_id="conversation-a",
            handler="chat",
            success=True,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
        )
    )
    feedback.add(
        "preference",
        "concise",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    strategies.add(
        Strategy(
            capability="chat",
            rule_text="be concise",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
        )
    )
    with meta._connect() as connection:
        connection.execute(
            """
            INSERT INTO meta_gaps(
                topic, tenant_id, workspace_id, principal_id, kind,
                suggested_action, seen_count, first_seen, last_seen
            ) VALUES ('pricing', 'tenant-a', 'workspace-a', '', 'unknown',
                      'ask_user', 1, '2026-01-01', '2026-01-01')
            """
        )
    registry.lifecycle.bind_auxiliary_stores(
        episodes=episodes,
        feedback=feedback,
        strategies=strategies,
        meta=meta,
    )

    report = registry.lifecycle.delete_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        confirmation="tenant-a:workspace-a",
    )

    assert report["auxiliary"] == {
        "episodes": 1,
        "feedback": 1,
        "strategies": 1,
        "meta": 1,
    }
