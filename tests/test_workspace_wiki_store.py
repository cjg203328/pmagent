"""Tests for Wiki source-of-truth and RAG projection behavior."""

from __future__ import annotations

import pytest

from artpm_agent.memory import (
    WikiPageConflictError,
    WorkspaceKnowledgeStore,
    WorkspaceWikiStore,
)
from artpm_agent.harness.memory_retrieval import retrieve_memory_context
from artpm_agent.tenancy import (
    TenantContext,
    TenantContextManager,
    WorkspaceAccessDenied,
)


def _stores(tmp_path):
    knowledge = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        enable_vector_search=False,
    )
    return knowledge, WorkspaceWikiStore(knowledge)


def test_draft_wiki_page_is_not_retrieved_until_published(tmp_path) -> None:
    knowledge, wiki = _stores(tmp_path)
    page = wiki.create_page(
        title="Texture handoff guide",
        markdown="Use crimsontexture naming for final handoff files.",
        workspace_id="studio-a",
        slug="texture-handoff",
    )

    assert page["status"] == "draft"
    assert page["sync"]["status"] == "not_indexed"
    assert knowledge.search("crimsontexture", workspace_id="studio-a") == []

    published = wiki.publish_page(
        page["id"],
        workspace_id="studio-a",
        expected_version=page["version"],
    )

    assert published["status"] == "published"
    assert published["sync"]["status"] == "synced"
    hits = knowledge.search("crimsontexture", workspace_id="studio-a")
    assert len(hits) == 1
    assert hits[0]["source"]["type"] == "wiki"
    assert hits[0]["source"]["id"] == page["id"]
    assert hits[0]["id"] == published["rag"]["resource_id"]
    injected = retrieve_memory_context(
        "crimsontexture",
        knowledge_store=knowledge,
        workspace_id="studio-a",
    )
    assert "Texture handoff guide" in injected
    assert "crimsontexture" in injected


def test_wiki_update_restore_and_archive_follow_rag_versions(tmp_path) -> None:
    knowledge, wiki = _stores(tmp_path)
    created = wiki.create_page(
        title="Asset review flow",
        markdown="The crimsontexture review is due before the daily standup.",
        workspace_id="studio-a",
        publish=True,
    )
    updated = wiki.update_page(
        created["id"],
        workspace_id="studio-a",
        markdown="The azureschedule review is due before the daily standup.",
        expected_version=created["version"],
        actor="producer-1",
    )

    assert updated["version"] == 2
    assert updated["sync"]["status"] == "synced"
    resource = knowledge.get_resource_by_source(
        source_type="wiki",
        source_id=created["id"],
        workspace_id="studio-a",
    )
    assert resource is not None
    assert resource["current_version"] == 2
    assert knowledge.search("crimsontexture", workspace_id="studio-a") == []
    assert knowledge.search("azureschedule", workspace_id="studio-a")

    restored = wiki.restore_version(
        created["id"],
        1,
        workspace_id="studio-a",
        expected_version=updated["version"],
        actor="producer-1",
    )
    assert restored["version"] == 3
    assert restored["sync"]["status"] == "synced"
    assert knowledge.search("crimsontexture", workspace_id="studio-a")

    archived = wiki.archive_page(
        created["id"],
        workspace_id="studio-a",
        expected_version=restored["version"],
    )
    assert archived["status"] == "archived"
    assert archived["sync"]["status"] == "synced"
    assert knowledge.search("crimsontexture", workspace_id="studio-a") == []
    archived_resource = knowledge.get_resource(
        archived["rag"]["resource_id"],
        workspace_id="studio-a",
    )
    assert archived_resource is not None
    assert archived_resource["status"] == "archived"


def test_wiki_writes_are_workspace_scoped_and_compare_and_swap(tmp_path) -> None:
    knowledge, wiki = _stores(tmp_path)
    first = wiki.create_page(
        title="Studio A glossary",
        markdown="asteraonly belongs to Studio A.",
        workspace_id="studio-a",
        slug="glossary",
        publish=True,
    )
    second = wiki.create_page(
        title="Studio B glossary",
        markdown="boralisonly belongs to Studio B.",
        workspace_id="studio-b",
        slug="glossary",
        publish=True,
    )

    assert knowledge.search("asteraonly", workspace_id="studio-a")
    assert knowledge.search("asteraonly", workspace_id="studio-b") == []
    assert knowledge.search("boralisonly", workspace_id="studio-b")
    assert first["slug"] == second["slug"] == "glossary"

    wiki.update_page(
        first["id"],
        workspace_id="studio-a",
        markdown="asteraonly has a revised Studio A workflow.",
        expected_version=first["version"],
    )
    with pytest.raises(WikiPageConflictError):
        wiki.update_page(
            first["id"],
            workspace_id="studio-a",
            markdown="stale write",
            expected_version=first["version"],
        )


def test_failed_wiki_projection_is_durable_and_reconciles(tmp_path, monkeypatch) -> None:
    knowledge, wiki = _stores(tmp_path)
    original_ingest = knowledge.ingest_resource

    def fail_ingest(**_kwargs):
        raise RuntimeError("temporary vector backend outage")

    monkeypatch.setattr(knowledge, "ingest_resource", fail_ingest)
    page = wiki.create_page(
        title="Delayed synchronization",
        markdown="recoverytoken must be retrievable after retry.",
        workspace_id="studio-a",
        publish=True,
    )

    assert page["status"] == "published"
    assert page["sync"]["status"] == "error"
    assert "temporary vector backend outage" in page["sync"]["error"]
    assert knowledge.search("recoverytoken", workspace_id="studio-a") == []

    monkeypatch.setattr(knowledge, "ingest_resource", original_ingest)
    summary = wiki.reconcile(workspace_id="studio-a")
    recovered = wiki.get_page(page["id"], workspace_id="studio-a")

    assert summary == {"attempted": 1, "synced": 1, "errors": 0}
    assert recovered is not None
    assert recovered["sync"]["status"] == "synced"
    assert knowledge.search("recoverytoken", workspace_id="studio-a")


def test_wiki_pages_and_projection_are_tenant_scoped(tmp_path) -> None:
    knowledge, wiki = _stores(tmp_path)
    tenant_a = wiki.create_page(
        title="Tenant A glossary",
        markdown="tenantalpha is private to tenant A.",
        tenant_id="tenant-a",
        workspace_id="shared-workspace",
        slug="glossary",
        publish=True,
    )
    tenant_b = wiki.create_page(
        title="Tenant B glossary",
        markdown="tenantbeta is private to tenant B.",
        tenant_id="tenant-b",
        workspace_id="shared-workspace",
        slug="glossary",
        publish=True,
    )

    assert tenant_a["tenant_id"] == "tenant-a"
    assert tenant_b["tenant_id"] == "tenant-b"
    assert wiki.get_page(
        tenant_a["id"], tenant_id="tenant-b", workspace_id="shared-workspace"
    ) is None
    assert wiki.get_page_by_slug(
        "glossary", tenant_id="tenant-a", workspace_id="shared-workspace"
    )["id"] == tenant_a["id"]
    assert wiki.list_versions(
        tenant_a["id"], tenant_id="tenant-b", workspace_id="shared-workspace"
    ) == []
    assert wiki.list_sync_events(
        tenant_a["id"], tenant_id="tenant-b", workspace_id="shared-workspace"
    ) == []

    assert knowledge.search(
        "tenantalpha", tenant_id="tenant-a", workspace_id="shared-workspace"
    )
    assert knowledge.search(
        "tenantalpha", tenant_id="tenant-b", workspace_id="shared-workspace"
    ) == []
    assert knowledge.get_resource(
        tenant_a["rag"]["resource_id"],
        tenant_id="tenant-b",
        workspace_id="shared-workspace",
    ) is None

    with pytest.raises(KeyError):
        wiki.archive_page(
            tenant_a["id"],
            tenant_id="tenant-b",
            workspace_id="shared-workspace",
        )
    assert wiki.get_page(
        tenant_a["id"], tenant_id="tenant-a", workspace_id="shared-workspace"
    )["status"] == "published"


def test_wiki_context_controls_tenant_for_sync_and_reconcile(tmp_path) -> None:
    knowledge, wiki = _stores(tmp_path)
    context = TenantContext(
        tenant_id="tenant-a", workspace_id="shared-workspace"
    )
    with TenantContextManager.use(context):
        page = wiki.create_page(
            title="Context-bound page",
            markdown="contextboundtoken",
            slug="context-bound",
            publish=True,
        )
        assert page["tenant_id"] == "tenant-a"
        with pytest.raises(WorkspaceAccessDenied):
            wiki.get_page(
                page["id"],
                tenant_id="tenant-b",
                workspace_id="shared-workspace",
            )
        assert wiki.reconcile(limit=10) == {
            "attempted": 0,
            "synced": 0,
            "errors": 0,
        }
