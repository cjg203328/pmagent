"""End-to-end regression tests for the public conversation gateway."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from artpm_agent.api import ChatOutcome, GatewayServices, create_app
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.security.permission_store import PermissionStore
from artpm_agent.workflows.store import WorkflowStore


def _headers(
    workspace_id: str = "local-default",
    *,
    tenant_id: str = "tenant-a",
    actor_id: str = "alice",
) -> dict[str, str]:
    return {
        "x-workspace-id": workspace_id,
        "x-tenant-id": tenant_id,
        "x-actor-id": actor_id,
        "x-actor-role": "user",
    }


def _add_workspace(store: ConversationStore, workspace_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with store._connection(write=True) as connection:  # noqa: SLF001 - fixture setup
        connection.execute(
            """
            INSERT INTO workspaces(id, profile_id, name, settings_json, created_at, updated_at)
            VALUES (?, 'local-default', ?, '{}', ?, ?)
            """,
            (workspace_id, workspace_id, now, now),
        )


@pytest.fixture()
def conversation_gateway(tmp_path: Path) -> SimpleNamespace:
    """Run the real HTTP, identity, persistence, and permission boundaries."""

    db_path = tmp_path / "conversation-flow.sqlite"
    conversations = ConversationStore(db_path)
    permissions = PermissionStore(db_path)
    workflows = WorkflowStore(db_path)
    _add_workspace(conversations, "workspace-b")
    commands = []
    executions = []

    def chat(command):
        commands.append(command)
        if command.message == "trigger private provider failure":
            raise RuntimeError("provider-secret at C:/private/provider/config")
        if command.message == "publish the confidential report":
            request = permissions.create_request(
                workspace_id=command.principal.workspace_id,
                conversation_id=command.conversation_id,
                turn_id=command.turn_id,
                agent_id="integration-agent",
                source="skill",
                action="skill.publish_report",
                resource={"report_id": "report-7"},
                risk="high",
                required_role="user",
                payload={
                    "skill_name": "publish_report",
                    "inputs": {"report_id": "report-7", "token": "server-secret"},
                },
                idempotency_key=f"publish:{command.turn_id}",
            )
            return ChatOutcome(
                response="Approval is required before publication.",
                awaiting_approval=True,
                handled_by="permission_gate",
                metadata={"permission_request_id": request.id},
            )
        return ChatOutcome(
            response=f"Acknowledged: {command.message}",
            handled_by="integration-adapter",
        )

    def execute_permission(request, tenant_context):
        executions.append((request.id, tenant_context.workspace_id))
        return {"published": request.resource["report_id"]}

    services = GatewayServices(
        conversations=conversations,
        permissions=permissions,
        workflows=workflows,
        chat_handler=chat,
        capability_provider=lambda: [],
        permission_executor=execute_permission,
        permission_executor_sources=frozenset({"skill"}),
    )
    return SimpleNamespace(
        client=TestClient(create_app(services)),
        services=services,
        commands=commands,
        executions=executions,
    )


def test_two_turn_conversation_is_persisted_under_one_identity(conversation_gateway):
    first = conversation_gateway.client.post(
        "/v1/chat",
        headers=_headers(),
        json={"message": "The project is Atlas."},
    )
    assert first.status_code == 200

    conversation_id = first.json()["conversation_id"]
    second = conversation_gateway.client.post(
        "/v1/chat",
        headers=_headers(),
        json={
            "message": "Prepare the next milestone.",
            "conversation_id": conversation_id,
        },
    )

    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id
    messages = conversation_gateway.services.conversations.list_messages(
        conversation_id,
        workspace_id="local-default",
    )
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [message["content"] for message in messages] == [
        "The project is Atlas.",
        "Acknowledged: The project is Atlas.",
        "Prepare the next milestone.",
        "Acknowledged: Prepare the next milestone.",
    ]
    assert messages[0]["turn_id"] == messages[1]["turn_id"]
    assert messages[2]["turn_id"] == messages[3]["turn_id"]
    assert messages[0]["turn_id"] != messages[2]["turn_id"]
    assert all(
        command.tenant_context.workspace_id == "local-default"
        and command.tenant_context.tenant_id == "tenant-a"
        for command in conversation_gateway.commands
    )


def test_conversation_and_permission_ids_cannot_cross_tenant_workspace(
    conversation_gateway,
):
    created = conversation_gateway.client.post(
        "/v1/chat",
        headers=_headers(),
        json={"message": "Workspace A private context."},
    )
    conversation_id = created.json()["conversation_id"]

    crossed = conversation_gateway.client.post(
        "/v1/chat",
        headers=_headers(
            "workspace-b",
            tenant_id="tenant-b",
            actor_id="bob",
        ),
        json={"message": "Read workspace A.", "conversation_id": conversation_id},
    )

    assert crossed.status_code == 404
    assert crossed.json()["error"]["code"] == "conversation_not_found"
    assert (
        conversation_gateway.services.conversations.list_messages(
            conversation_id,
            workspace_id="workspace-b",
        )
        == []
    )


def test_high_risk_approval_cannot_be_forged_bypassed_or_replayed(
    conversation_gateway,
):
    requested = conversation_gateway.client.post(
        "/v1/chat",
        headers=_headers(),
        json={"message": "publish the confidential report"},
    )

    assert requested.status_code == 202
    assert requested.json()["awaiting_approval"] is True
    request_id = requested.json()["permission_request_id"]

    forged = conversation_gateway.client.post(
        f"/v1/permissions/{request_id}/approve",
        headers=_headers(),
        json={
            "expected_version": 0,
            "acknowledged_risk": "high",
            "approved": True,
        },
    )
    assert forged.status_code == 422

    missing_acknowledgement = conversation_gateway.client.post(
        f"/v1/permissions/{request_id}/approve",
        headers=_headers(),
        json={"expected_version": 0},
    )
    assert missing_acknowledgement.status_code == 422
    assert missing_acknowledgement.json()["error"]["code"] == (
        "invalid_permission_request"
    )
    assert conversation_gateway.executions == []

    crossed = conversation_gateway.client.get(
        f"/v1/permissions/{request_id}",
        headers=_headers(
            "workspace-b",
            tenant_id="tenant-b",
            actor_id="bob",
        ),
    )
    assert crossed.status_code == 404

    approved = conversation_gateway.client.post(
        f"/v1/permissions/{request_id}/approve",
        headers=_headers(),
        json={"expected_version": 0, "acknowledged_risk": "high"},
    )
    assert approved.status_code == 200
    assert approved.json()["item"]["status"] == "completed"
    assert conversation_gateway.executions == [(request_id, "local-default")]

    replayed = conversation_gateway.client.post(
        f"/v1/permissions/{request_id}/approve",
        headers=_headers(),
        json={"expected_version": 0, "acknowledged_risk": "high"},
    )
    assert replayed.status_code == 409
    assert conversation_gateway.executions == [(request_id, "local-default")]


def test_provider_failure_returns_and_persists_a_safe_error_callback(
    conversation_gateway,
):
    response = conversation_gateway.client.post(
        "/v1/chat",
        headers=_headers(),
        json={"message": "trigger private provider failure"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "chat_handler_failed"
    assert response.json()["error"]["message"]
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
    assert "provider-secret" not in response.text
    assert "C:/private" not in response.text

    conversation = conversation_gateway.services.conversations.list_conversations(
        workspace_id="local-default",
        limit=1,
    )[0]
    messages = conversation_gateway.services.conversations.list_messages(
        conversation["id"],
        workspace_id="local-default",
    )
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[-1]["status"] == "error"
    assert messages[-1]["content"]
    assert "provider-secret" not in str(messages[-1])
    assert "C:/private" not in str(messages[-1])
