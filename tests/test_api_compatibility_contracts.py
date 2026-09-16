"""Regression tests for API compatibility and the static OpenAPI contract."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from artpm_agent.api import ChatOutcome, GatewayServices, create_app
from artpm_agent.api.api_docs import get_openapi_spec
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.security.permission_store import PermissionStore
from artpm_agent.workflows.store import WorkflowStore


def _services(db_path: Path) -> GatewayServices:
    return GatewayServices(
        conversations=ConversationStore(db_path),
        permissions=PermissionStore(db_path),
        workflows=WorkflowStore(db_path),
        chat_handler=lambda _command: ChatOutcome(response="ok"),
        capability_provider=lambda: [],
    )


def _headers(*, workspace: str = "local-default") -> dict[str, str]:
    return {
        "x-workspace-id": workspace,
        "x-actor-id": "admin-user",
        "x-actor-role": "admin",
        "x-tenant-id": "tenant-a",
    }


def test_gateway_services_preserves_legacy_positional_field_order():
    values = [object() for _ in range(14)]

    services = GatewayServices(*values)

    assert services.conversations is values[0]
    assert services.permissions is values[1]
    assert services.workflows is values[2]
    assert services.chat_handler is values[3]
    assert services.capability_provider is values[4]
    assert services.chat_async_handler is values[5]
    assert services.workflow_capability_provider is values[6]
    assert services.permission_executor is values[7]
    assert services.permission_executor_sources is values[8]
    assert services.workflow_engine is values[9]
    assert services.workflow_engine_factory is values[10]
    assert services.health_handler is values[11]
    assert services.close_handler is values[12]
    assert services.event_bus is values[13]
    assert services.knowledge_search_handler is None
    assert services.deployment_capability_provider is None


def test_workspace_create_rejects_identifiers_outside_gateway_contract(tmp_path: Path):
    client = TestClient(create_app(_services(tmp_path / "gateway.sqlite")))

    invalid_workspace = client.post(
        "/v1/workspaces",
        headers=_headers(),
        json={"id": "bad workspace", "name": "Bad"},
    )
    assert invalid_workspace.status_code == 400
    assert invalid_workspace.json()["error"]["code"] == "invalid_workspace"

    invalid_profile = client.post(
        "/v1/workspaces",
        headers=_headers(),
        json={"id": "valid-workspace", "name": "Valid", "profile_id": "bad/profile"},
    )
    assert invalid_profile.status_code == 400
    assert invalid_profile.json()["error"]["code"] == "invalid_workspace"


def test_static_openapi_contract_matches_workspace_and_search_routes():
    spec = get_openapi_spec()
    paths = spec["paths"]
    schemas = spec["components"]["schemas"]

    assert "/" in paths
    assert "/v1/workspaces" in paths
    assert "/v1/search" in paths
    assert "/v1/chat/stream" in paths
    assert "/embed/{channel}/exchange" in paths
    assert "/api/v1/workspaces" not in paths

    workspace_create = paths["/v1/workspaces"]["post"]
    assert workspace_create["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WorkspaceCreateRequest"
    }
    assert workspace_create["responses"]["403"]["description"]

    workspace_list = paths["/v1/workspaces"]["get"]
    assert workspace_list["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WorkspaceListResponse"
    }

    search = paths["/v1/search"]["post"]
    assert search["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/KnowledgeSearchRequest"
    }
    assert search["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/KnowledgeSearchResponse"
    }
    assert schemas["WorkspaceCreateRequest"]["required"] == ["id", "name"]
    assert schemas["KnowledgeSearchRequest"]["required"] == ["query"]
    assert schemas["ChatStreamRequest"]["allOf"][0]["$ref"].endswith("/ChatRequest")
