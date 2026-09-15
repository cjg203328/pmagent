from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from artpm_agent.api import GatewayServices, create_app
from artpm_agent.api.services import ChatOutcome
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore
from artpm_agent.security.permission_store import PermissionStore
from artpm_agent.workflows.store import WorkflowStore
from artpm_agent.retrieval import RetrievalPlan, SearchTarget, WorkspaceRetriever


def _headers(workspace: str = "local-default", *, role: str = "admin") -> dict[str, str]:
    return {
        "x-workspace-id": workspace,
        "x-actor-id": "test-user",
        "x-actor-role": role,
        "x-tenant-id": "tenant-a",
    }


def test_workspace_store_creates_tenant_owned_workspace(tmp_path: Path):
    store = ConversationStore(tmp_path / "conversation.sqlite")

    created = store.create_workspace(
        "art-team",
        "Art Team",
        tenant_id="tenant-a",
        settings={"theme": "dark"},
    )

    assert created["id"] == "art-team"
    assert created["tenant_id"] == "tenant-a"
    assert created["settings"] == {"theme": "dark"}
    assert store.get_workspace("art-team")["name"] == "Art Team"


def test_retriever_normalizes_and_filters_scope():
    class FakeStore:
        def search(self, *_args, **_kwargs):
            return [
                {
                    "id": "resource-a",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "title": "Accepted source",
                    "text": "Relevant context",
                    "score": 0.8,
                    "retrieval_mode": "vector",
                    "source": {"uri": "file://source.md"},
                    "resource_type": "document",
                },
                {
                    "id": "cross-scope",
                    "tenant_id": "tenant-b",
                    "workspace_id": "workspace-b",
                    "title": "Must be dropped",
                    "text": "Secret",
                    "score": 1.0,
                },
            ]

    plan = RetrievalPlan(
        query="context",
        target=SearchTarget(tenant_id="tenant-a", workspace_id="workspace-a"),
    )
    hits = WorkspaceRetriever(FakeStore()).search(plan)

    assert len(hits) == 1
    assert hits[0].citation == {
        "id": "resource-a",
        "workspace_id": "workspace-a",
        "title": "Accepted source",
        "source_uri": "file://source.md",
        "resource_type": "document",
        "score": 0.8,
        "match_type": "vector",
    }


def test_retriever_applies_confidence_floor(tmp_path: Path):
    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.sqlite",
        enable_vector_search=False,
    )
    low = store.ingest_resource(
        title="Low confidence",
        searchable_text="needle",
        resource_id="low-confidence",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    high = store.ingest_resource(
        title="High confidence",
        searchable_text="needle",
        resource_id="high-confidence",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    assert store.set_confidence(
        low["id"], 0.1, tenant_id="tenant-a", workspace_id="workspace-a"
    )
    assert store.set_confidence(
        high["id"], 0.9, tenant_id="tenant-a", workspace_id="workspace-a"
    )

    plan = RetrievalPlan(
        query="needle",
        target=SearchTarget(tenant_id="tenant-a", workspace_id="workspace-a"),
        confidence_floor=0.5,
    )
    hits = WorkspaceRetriever(store).search(plan)

    assert [hit.id for hit in hits] == ["high-confidence"]


def test_workspace_and_search_api_contracts(tmp_path: Path):
    db_path = tmp_path / "gateway.sqlite"
    conversations = ConversationStore(db_path)
    services = GatewayServices(
        conversations=conversations,
        permissions=PermissionStore(db_path),
        workflows=WorkflowStore(db_path),
        chat_handler=lambda _command: ChatOutcome(response="ok"),
        capability_provider=lambda: [],
        knowledge_search_handler=lambda principal, payload: [
            {
                "id": "resource-a",
                "tenant_id": principal.tenant_id,
                "workspace_id": principal.workspace_id,
                "title": payload["query"],
                "text": "result",
                "score": 0.9,
                "match_type": "literal",
            }
        ],
    )
    client = TestClient(create_app(services))

    created = client.post(
        "/v1/workspaces",
        headers=_headers(),
        json={"id": "art-team", "name": "Art Team"},
    )
    assert created.status_code == 201
    assert created.json()["tenant_id"] == "tenant-a"

    listed = client.get("/v1/workspaces", headers=_headers("art-team"))
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["items"]} >= {"art-team"}

    custom_profile = client.post(
        "/v1/workspaces",
        headers=_headers(),
        json={"id": "custom-team", "name": "Custom Team", "profile_id": "studio"},
    )
    assert custom_profile.status_code == 201
    listed_again = client.get("/v1/workspaces", headers=_headers("art-team"))
    assert listed_again.status_code == 200
    assert {item["id"] for item in listed_again.json()["items"]} >= {
        "art-team",
        "custom-team",
    }

    searched = client.post(
        "/v1/search",
        headers=_headers("art-team", role="user"),
        json={"query": "报价", "limit": 5},
    )
    assert searched.status_code == 200
    assert searched.json()["workspace_id"] == "art-team"
    assert searched.json()["items"][0]["id"] == "resource-a"


def test_workspace_list_does_not_expose_unbound_legacy_rows(tmp_path: Path):
    store = ConversationStore(tmp_path / "legacy.sqlite")
    with store._connection(write=True) as connection:  # noqa: SLF001 - fixture setup
        connection.execute(
            """
            INSERT INTO workspaces(
                id, profile_id, name, tenant_id, settings_json, created_at, updated_at
            ) VALUES ('legacy-unbound', 'local-default', 'Legacy', NULL, '{}',
                      '2026-01-01', '2026-01-01')
            """
        )
    services = GatewayServices(
        conversations=store,
        permissions=PermissionStore(store.db_path),
        workflows=WorkflowStore(store.db_path),
        chat_handler=lambda _command: ChatOutcome(response="ok"),
        capability_provider=lambda: [],
    )
    client = TestClient(create_app(services))

    listed = client.get("/v1/workspaces", headers=_headers())

    assert listed.status_code == 200
    assert "legacy-unbound" not in {item["id"] for item in listed.json()["items"]}
