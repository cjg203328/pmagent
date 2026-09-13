"""Contract and isolation tests for the REST gateway."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from artpm_agent.api import (
    ChatOutcome,
    GatewayServices,
    IdentityError,
    TrustedHeaderIdentityResolver,
    create_app,
)
from artpm_agent.api.services import (
    ChatCommand,
    DefaultGatewayRuntime,
    RequestPrincipal,
    _store_status,
)
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.security.permission_store import PermissionStore
from artpm_agent.workflows.engine import WorkflowEngine
from artpm_agent.workflows.store import WorkflowStore


def _headers(workspace: str = "local-default", *, actor: str = "alice", role: str = "user"):
    return {
        "x-workspace-id": workspace,
        "x-actor-id": actor,
        "x-actor-role": role,
    }


def _add_workspace(store: ConversationStore, workspace_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with store._connection(write=True) as connection:  # noqa: SLF001 - test fixture setup
        connection.execute(
            """
            INSERT INTO workspaces(id, profile_id, name, settings_json, created_at, updated_at)
            VALUES (?, 'local-default', ?, '{}', ?, ?)
            """,
            (workspace_id, workspace_id, now, now),
        )


@pytest.fixture()
def gateway(tmp_path: Path):
    db_path = tmp_path / "gateway.sqlite"
    conversations = ConversationStore(db_path)
    permissions = PermissionStore(db_path)
    workflows = WorkflowStore(db_path)
    _add_workspace(conversations, "tenant-b")
    calls: list[str] = []

    def chat(command):
        calls.append(command.principal.workspace_id)
        return ChatOutcome(
            response="gateway response",
            handled_by="test-adapter",
            metadata={"echo": command.message},
        )

    def capabilities():
        return [
            {
                "name": "read_skill",
                "risk": "low",
                "read_only": True,
                "requires_approval": False,
            }
        ]

    def execute_permission(request):
        return {"success": True, "request_id": request.id}

    engine = WorkflowEngine(
        workflows,
        lambda skill_id, inputs: {
            "success": True,
            "skill_id": skill_id,
            "inputs": inputs,
        },
    )
    services = GatewayServices(
        conversations=conversations,
        permissions=permissions,
        workflows=workflows,
        chat_handler=chat,
        capability_provider=capabilities,
        permission_executor=execute_permission,
        permission_executor_sources=frozenset({"skill"}),
        workflow_engine=engine,
    )
    app = create_app(services)
    return TestClient(app), services, calls


def test_health_and_identity_contract(gateway):
    client, _, _ = gateway
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.headers["x-request-id"]

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True

    missing = client.get("/v1/capabilities")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_identity"

    malformed = client.get(
        "/v1/capabilities",
        headers={"x-workspace-id": "bad workspace", "x-actor-id": "alice"},
    )
    assert malformed.status_code == 401


def test_insecure_header_identity_is_local_only_and_rejects_forwarded_requests():
    resolver = TrustedHeaderIdentityResolver()
    headers = {
        "x-workspace-id": "workspace-a",
        "x-tenant-id": "tenant-a",
        "x-actor-id": "alice",
        "x-actor-role": "admin",
    }

    local = resolver(
        SimpleNamespace(headers=headers, client=SimpleNamespace(host="127.0.0.1"))
    )
    assert local.workspace_id == "workspace-a"
    assert local.actor_role == "admin"

    with pytest.raises(IdentityError, match="gateway token"):
        resolver(
            SimpleNamespace(
                headers=headers,
                client=SimpleNamespace(host="198.51.100.24"),
            )
        )

    with pytest.raises(IdentityError, match="gateway token"):
        resolver(
            SimpleNamespace(
                headers={**headers, "x-forwarded-for": "198.51.100.24"},
                client=SimpleNamespace(host="127.0.0.1"),
            )
        )


def test_gateway_secret_authenticates_remote_identity():
    resolver = TrustedHeaderIdentityResolver("shared-secret")
    request = SimpleNamespace(
        headers={
            "x-workspace-id": "workspace-a",
            "x-tenant-id": "tenant-a",
            "x-actor-id": "service-a",
            "x-actor-role": "admin",
            "x-gateway-token": "shared-secret",
        },
        client=SimpleNamespace(host="198.51.100.24"),
    )

    assert resolver(request).tenant_id == "tenant-a"

    request.headers["x-gateway-token"] = "forged"
    with pytest.raises(IdentityError, match="missing or invalid"):
        resolver(request)


def test_readiness_rejects_degraded_services(gateway):
    client, services, _ = gateway
    services.health_handler = lambda: {
        "status": "degraded",
        "checks": {"conversation_store": {"status": "error"}},
    }

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert ready.status_code == 503
    assert ready.json()["ready"] is False


def test_default_runtime_health_probes_database_and_required_schema(tmp_path):
    runtime = object.__new__(DefaultGatewayRuntime)
    missing_path = tmp_path / "missing.sqlite"
    store = type("Store", (), {"db_path": str(missing_path)})()
    runtime.conversations = store
    runtime.permissions = store
    runtime.workflows = store

    result = runtime.health()

    assert result["status"] == "degraded"
    assert not missing_path.exists()
    assert all(
        result["checks"][name]["status"] == "error"
        for name in ("conversation_store", "permission_store", "workflow_store")
    )


def test_store_health_probe_always_closes_sqlite_connection(tmp_path, monkeypatch):
    class ProbeConnection:
        closed = False

        def execute(self, _query, _parameters=()):
            return self

        def fetchone(self):
            return (1,)

        def close(self):
            self.closed = True

    connection = ProbeConnection()
    path = tmp_path / "health.sqlite"
    path.touch()
    monkeypatch.setattr(
        "artpm_agent.api.services.sqlite3.connect",
        lambda *_args, **_kwargs: connection,
    )

    assert _store_status(type("Store", (), {"db_path": str(path)})()) == {
        "status": "ok"
    }
    assert connection.closed is True


def test_route_errors_and_cors_use_the_gateway_contract(gateway):
    client, _, _ = gateway

    missing = client.get("/v1/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "route_not_found"
    assert missing.json()["error"]["request_id"] == missing.headers["x-request-id"]

    method = client.put("/health")
    assert method.status_code == 405
    assert method.json()["error"]["code"] == "method_not_allowed"

    preflight = client.options(
        "/v1/chat",
        headers={
            "Origin": "http://localhost:8501",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:8501"

    blocked = client.options(
        "/v1/chat",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in blocked.headers


def test_failed_chat_outcome_is_structured_and_does_not_leak_provider_error(gateway):
    client, services, _ = gateway
    services.chat_handler = lambda _command: ChatOutcome(
        response="",
        success=False,
        error="secret provider stack trace",
    )

    response = client.post(
        "/v1/chat",
        headers=_headers(),
        json={"message": "hello"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "chat_failed"
    assert "secret provider stack trace" not in response.text
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
    conversation = services.conversations.list_conversations(
        workspace_id="local-default", limit=1
    )[0]
    messages = services.conversations.list_messages(
        conversation["id"], workspace_id="local-default"
    )
    assert messages[-1]["metadata"]["error"] == "chat_failed"
    assert "secret provider" not in str(messages[-1]["metadata"])


def test_chat_handler_exception_persists_a_safe_error_callback(gateway):
    client, services, _ = gateway

    def fail_chat(_command):
        raise RuntimeError("secret provider stack and C:/private/path")

    services.chat_handler = fail_chat
    response = client.post(
        "/v1/chat",
        headers=_headers(),
        json={"message": "hello"},
    )

    assert response.status_code == 502
    conversation = services.conversations.list_conversations(
        workspace_id="local-default",
        limit=1,
    )[0]
    messages = services.conversations.list_messages(
        conversation["id"],
        workspace_id="local-default",
    )
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[-1]["status"] == "error"
    assert messages[-1]["content"] == "请求处理失败，请稍后重试。"
    assert "secret provider" not in str(messages[-1])


def test_async_chat_handler_is_supported_by_the_gateway(gateway):
    client, services, _ = gateway

    async def async_chat(command):
        return ChatOutcome(
            response=f"async:{command.message}",
            handled_by="async-test-adapter",
        )

    services.chat_async_handler = async_chat
    response = client.post(
        "/v1/chat",
        headers=_headers(),
        json={"message": "hello async"},
    )

    assert response.status_code == 200
    assert response.json()["response"] == "async:hello async"


def test_validation_errors_do_not_echo_sensitive_request_values(gateway):
    client, _, _ = gateway
    secret = "sk-super-secret-value"
    response = client.post(
        "/v1/chat",
        headers=_headers(),
        json={"message": ("x" * 8001) + secret, "unexpected": secret},
    )

    assert response.status_code == 422
    assert secret not in response.text
    assert len(response.text) < 2000


def test_production_gateway_fails_closed_when_shared_secret_is_missing(gateway):
    _, services, _ = gateway
    with pytest.raises(RuntimeError, match="SHARED_SECRET"):
        create_app(services, require_gateway_secret=True)


def test_cors_wildcard_is_rejected(monkeypatch, gateway):
    _, services, _ = gateway
    monkeypatch.setenv("ARTPM_CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="explicit origins"):
        create_app(services)


def test_chat_is_persisted_and_workspace_scoped(gateway):
    client, services, calls = gateway
    response = client.post(
        "/v1/chat",
        headers=_headers("tenant-b"),
        json={"message": "hello"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "gateway response"
    assert calls == ["tenant-b"]
    conversation_id = body["conversation_id"]
    assert services.conversations.get_conversation(
        conversation_id, workspace_id="tenant-b"
    ) is not None

    # The same identifier cannot be read from another tenant.
    crossed = client.get(
        f"/v1/permissions?conversation_id={conversation_id}",
        headers=_headers("local-default"),
    )
    assert crossed.status_code == 200
    assert crossed.json()["items"] == []


def test_same_workspace_cannot_be_reused_by_another_tenant(gateway):
    client, services, _ = gateway
    _add_workspace(services.conversations, "shared-workspace")
    tenant_a = {
        **_headers("shared-workspace", actor="alice"),
        "x-tenant-id": "tenant-a",
    }
    tenant_b = {
        **_headers("shared-workspace", actor="bob"),
        "x-tenant-id": "tenant-b",
    }

    created = client.post("/v1/chat", headers=tenant_a, json={"message": "private"})
    assert created.status_code == 200

    crossed = client.post(
        "/v1/chat",
        headers=tenant_b,
        json={
            "message": "cross-tenant access",
            "conversation_id": created.json()["conversation_id"],
        },
    )
    assert crossed.status_code == 403
    assert crossed.json()["error"]["code"] == "workspace_tenant_mismatch"


def test_default_runtime_excludes_current_user_message_from_history(monkeypatch):
    import threading
    from unittest.mock import Mock

    from artpm_agent.harness import BaseHarnessRuntime, TurnResult
    import artpm_agent.harness as harness

    runtime = object.__new__(DefaultGatewayRuntime)
    runtime._lock = threading.RLock()
    runtime.conversations = Mock()
    runtime.permissions = Mock()
    runtime.conversations.build_context.return_value = []
    agent = BaseHarnessRuntime()
    monkeypatch.setattr(runtime, "_ensure_agent", lambda: agent)
    monkeypatch.setattr(runtime, "_ensure_workflow_runtime", lambda _tenant: (None, None))
    monkeypatch.setattr(
        harness,
        "run_turn",
        lambda *_args, **_kwargs: TurnResult(response="ok", handled_by="test"),
    )
    principal = RequestPrincipal(workspace_id="workspace-a", actor_id="alice")
    command = ChatCommand(
        principal=principal,
        conversation_id="conversation-a",
        turn_id="turn-a",
        message="current question",
        tenant_context=principal.tenant_context(),
        before_message_id=42,
    )

    outcome = runtime.chat(command)

    assert outcome.response == "ok"
    runtime.conversations.build_context.assert_called_once_with(
        "conversation-a",
        before_message_id=42,
        workspace_id="workspace-a",
    )


def test_default_runtime_records_scoped_episode(monkeypatch):
    import threading
    from unittest.mock import Mock

    import artpm_agent.harness as harness
    from artpm_agent.harness import BaseHarnessRuntime, TurnResult
    from artpm_agent.harness.outcome_recorder import default_episode_db_path
    from artpm_agent.memory.episode_store import EpisodeStore

    runtime = object.__new__(DefaultGatewayRuntime)
    runtime._lock = threading.RLock()
    runtime.conversations = Mock()
    runtime.permissions = Mock()
    runtime.conversations.build_context.return_value = []
    agent = BaseHarnessRuntime()
    monkeypatch.setattr(runtime, "_ensure_agent", lambda: agent)
    monkeypatch.setattr(runtime, "_ensure_workflow_runtime", lambda _tenant: (None, None))
    monkeypatch.setattr(
        harness,
        "run_turn",
        lambda *_args, **_kwargs: TurnResult(response="ok", handled_by="test"),
    )
    principal = RequestPrincipal(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="alice",
    )
    command = ChatCommand(
        principal=principal,
        conversation_id="conversation-a",
        turn_id="turn-a",
        message="current question",
        tenant_context=principal.tenant_context(),
    )

    assert runtime.chat(command).response == "ok"

    episodes = EpisodeStore(default_episode_db_path()).recent(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="alice",
    )
    assert [(episode.turn_id, episode.handler) for episode in episodes] == [
        ("turn-a", "test")
    ]


def test_default_runtime_uses_tenant_scoped_harness_adapter(monkeypatch):
    import threading
    from unittest.mock import Mock

    import artpm_agent.harness as harness
    from artpm_agent.harness.runtime import LegacyAgentRuntimeAdapter
    from artpm_agent.harness import TurnResult

    class Router:
        skills = {}

        def __init__(self):
            self.bound = None

        def for_tenant(self, context):
            scoped = Router()
            scoped.bound = context
            return scoped

    class Agent:
        def __init__(self):
            self.router = Router()

    runtime = object.__new__(DefaultGatewayRuntime)
    runtime._lock = threading.RLock()
    runtime.conversations = Mock()
    runtime.permissions = Mock()
    runtime.conversations.build_context.return_value = []
    agent = Agent()
    monkeypatch.setattr(runtime, "_ensure_agent", lambda: agent)
    monkeypatch.setattr(runtime, "_ensure_workflow_runtime", lambda _tenant: (None, None))
    captured = {}

    def capture(ctx, **_kwargs):
        captured["ctx"] = ctx
        return TurnResult(response="ok", handled_by="test")

    monkeypatch.setattr(harness, "run_turn", capture)
    principal = RequestPrincipal(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="alice",
    )
    command = ChatCommand(
        principal=principal,
        conversation_id="conversation-a",
        turn_id="turn-a",
        message="current question",
        tenant_context=principal.tenant_context(),
    )

    assert runtime.chat(command).response == "ok"
    context = captured["ctx"]
    assert context.agent is None
    assert isinstance(context.runtime, LegacyAgentRuntimeAdapter)
    assert context.runtime._router.bound == principal.tenant_context()


def test_capabilities_are_available_without_initializing_agent(gateway):
    client, _, _ = gateway
    response = client.get("/v1/capabilities", headers=_headers())
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "read_skill"


def test_capability_runtime_errors_are_not_hidden_by_static_fallback(monkeypatch):
    runtime = object.__new__(DefaultGatewayRuntime)
    monkeypatch.setattr(
        runtime,
        "_ensure_agent",
        lambda: (_ for _ in ()).throw(RuntimeError("provider configuration is broken")),
    )

    with pytest.raises(RuntimeError, match="provider configuration"):
        runtime.capabilities()


def test_capability_import_errors_use_the_explicit_static_fallback(monkeypatch):
    runtime = object.__new__(DefaultGatewayRuntime)
    monkeypatch.setattr(
        runtime,
        "_ensure_agent",
        lambda: (_ for _ in ()).throw(ImportError("optional agent dependency missing")),
    )

    entries = runtime.capabilities()

    assert entries
    assert all(item["name"] for item in entries)


def test_permission_lookup_maps_sqlite_failures_to_storage_unavailable(gateway, monkeypatch):
    client, services, _ = gateway

    def fail_get(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(services.permissions, "get", fail_get)
    response = client.get(
        "/v1/permissions/request-a",
        headers=_headers(),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert "database is locked" not in response.text


def test_workflow_run_lookup_maps_sqlite_failures_to_storage_unavailable(gateway, monkeypatch):
    client, services, _ = gateway

    def fail_get_run(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(services.workflows, "get_run", fail_get_run)
    response = client.get(
        "/v1/workflow-runs/run-a",
        headers=_headers(),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert "database is locked" not in response.text


def test_permission_approval_is_cas_once_and_tenant_scoped(gateway):
    client, services, _ = gateway
    request = services.permissions.create_request(
        workspace_id="local-default",
        conversation_id="conversation-a",
        turn_id="turn-a",
        agent_id="agent-a",
        source="skill",
        action="skill.write_skill",
        resource={"target": "record"},
        risk="high",
        required_role="user",
        payload={"skill_name": "write_skill", "inputs": {"token": "secret"}},
        idempotency_key="api-test-permission",
    )

    listed = client.get("/v1/permissions", headers=_headers())
    assert listed.status_code == 200
    assert listed.json()["items"][0]["redacted_arguments"]["inputs"]["token"] == "[REDACTED]"

    # Actor/payload fields are not part of the contract and cannot be used to
    # self-authorize a model-generated request.
    forged = client.post(
        f"/v1/permissions/{request.id}/approve",
        headers=_headers(),
        json={"expected_version": 0, "approved": True, "payload": {}},
    )
    assert forged.status_code == 422

    approved = client.post(
        f"/v1/permissions/{request.id}/approve",
        headers=_headers(),
        json={"expected_version": 0},
    )
    assert approved.status_code == 422
    assert approved.json()["error"]["code"] == "invalid_permission_request"

    approved = client.post(
        f"/v1/permissions/{request.id}/approve",
        headers=_headers(),
        json={"expected_version": 0, "acknowledged_risk": "high"},
    )
    assert approved.status_code == 200
    assert approved.json()["item"]["status"] == "completed"

    replay = client.post(
        f"/v1/permissions/{request.id}/approve",
        headers=_headers(),
        json={"expected_version": 0, "acknowledged_risk": "high"},
    )
    assert replay.status_code == 409

    crossed = client.get(
        f"/v1/permissions/{request.id}",
        headers=_headers("tenant-b", actor="bob"),
    )
    assert crossed.status_code == 404


def test_unsupported_permission_source_is_rejected_before_claim(gateway):
    client, services, _ = gateway
    request = services.permissions.create_request(
        workspace_id="local-default",
        conversation_id="conversation-a",
        turn_id="turn-tool",
        agent_id="agent-a",
        source="tool",
        action="tool.publish_report",
        resource={"tool": "publish_report"},
        risk="medium",
        required_role="user",
        payload={"tool_name": "publish_report", "arguments": {"project_id": 7}},
        idempotency_key="unsupported-tool-source",
    )

    response = client.post(
        f"/v1/permissions/{request.id}/approve",
        headers=_headers(),
        json={"expected_version": request.state_version},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_permission_source"
    current = services.permissions.get(request.id, workspace_id="local-default")
    assert current.status == "pending"
    assert current.execution_id is None


def test_failed_permission_execution_does_not_leak_internal_error(gateway):
    client, services, _ = gateway

    def fail_execution(_request):
        raise RuntimeError("provider-secret stack and C:/private/path")

    services.permission_executor = fail_execution
    request = services.permissions.create_request(
        workspace_id="local-default",
        conversation_id="conversation-a",
        turn_id="turn-failure",
        agent_id="agent-a",
        source="skill",
        action="skill.write_skill",
        resource={"target": "record"},
        risk="medium",
        required_role="user",
        payload={"skill_name": "write_skill", "inputs": {}},
        idempotency_key="permission-error-redaction",
    )

    response = client.post(
        f"/v1/permissions/{request.id}/approve",
        headers=_headers(),
        json={"expected_version": 0},
    )
    assert response.status_code == 502

    fetched = client.get(
        f"/v1/permissions/{request.id}",
        headers=_headers(),
    )
    assert fetched.status_code == 200
    rendered = fetched.text
    assert "provider-secret" not in rendered
    assert "C:/private/path" not in rendered
    assert fetched.json()["item"]["error"] == "approved_operation_failed"


def test_workflow_definition_and_run_are_scoped(gateway):
    client, services, _ = gateway
    conversation = services.conversations.create_conversation(
        "Workflow API", workspace_id="tenant-b"
    )
    definition = {
        "id": "tenant_quote",
        "version": 1,
        "name": "Tenant quote",
        "description": "Read-only tenant workflow",
        "read_only": True,
        "trigger": {"always": True},
        "steps": [
            {
                "id": "calculate",
                "skill_id": "quote_calculator",
                "capability": "quote.calculate",
                "input_map": {"amount": "$input.amount"},
            }
        ],
    }
    denied = client.post("/v1/workflows", headers=_headers("tenant-b"), json=definition)
    assert denied.status_code == 403
    created = client.post(
        "/v1/workflows",
        headers=_headers("tenant-b", actor="admin", role="admin"),
        json=definition,
    )
    assert created.status_code == 200
    assert created.json()["item"]["workspace_id"] == "tenant-b"

    local_list = client.get("/v1/workflows", headers=_headers("local-default"))
    assert all(item["id"] != "tenant_quote" for item in local_list.json()["items"])
    tenant_list = client.get("/v1/workflows", headers=_headers("tenant-b"))
    assert any(item["id"] == "tenant_quote" for item in tenant_list.json()["items"])

    run = client.post(
        "/v1/workflows/tenant_quote/runs",
        headers=_headers("tenant-b"),
        json={
            "conversation_id": conversation["id"],
            "input_data": {"amount": 10},
        },
    )
    assert run.status_code == 200
    assert run.json()["run"]["workspace_id"] == "tenant-b"

    crossed = client.post(
        "/v1/workflows/tenant_quote/runs",
        headers=_headers("local-default"),
        json={"conversation_id": conversation["id"]},
    )
    assert crossed.status_code == 404


def test_workflow_versions_and_risk_fields_are_server_owned(gateway):
    client, _, _ = gateway
    definition = {
        "id": "server_owned_version",
        "version": 99,
        "name": "Server-owned workflow",
        "description": "First revision",
        "read_only": False,
        "trigger": {"always": True},
        "steps": [
            {
                "id": "calculate",
                "skill_id": "quote_calculator",
                "capability": "quote.calculate",
                "side_effect": True,
                "approval": "admin",
                "input_map": {},
            }
        ],
    }
    headers = _headers(actor="admin", role="admin")

    first = client.post("/v1/workflows", headers=headers, json=definition)
    definition["description"] = "Second revision"
    second = client.post("/v1/workflows", headers=headers, json=definition)

    assert first.status_code == 200
    assert first.json()["item"]["version"] == 1
    assert first.json()["item"]["read_only"] is True
    assert first.json()["item"]["steps"][0]["side_effect"] is False
    assert first.json()["item"]["steps"][0]["approval"] == "none"
    assert second.status_code == 200
    assert second.json()["item"]["version"] == 2


def test_workflow_input_validation_is_mapped(gateway):
    client, _, _ = gateway
    response = client.post(
        "/v1/workflows/unknown/runs",
        headers=_headers(),
        json={"conversation_id": "missing"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"


def test_workflow_risk_fields_are_derived_server_side(gateway):
    client, _, _ = gateway
    response = client.post(
        "/v1/workflows",
        headers=_headers(actor="admin", role="admin"),
        json={
            "id": "dispatch_policy",
            "version": 1,
            "name": "Dispatch policy",
            "description": "The client attempts to downgrade this step.",
            "read_only": True,
            "steps": [
                {
                    "id": "send",
                    "skill_id": "reminder_dispatch",
                    "capability": "reminders.dispatch",
                    "side_effect": False,
                    "approval": "none",
                }
            ],
        },
    )
    assert response.status_code == 200
    item = response.json()["item"]
    assert item["read_only"] is False
    assert item["steps"][0]["side_effect"] is True
    assert item["steps"][0]["approval"] == "user"


def test_service_actor_cannot_confirm_permission(gateway):
    client, services, _ = gateway
    request = services.permissions.create_request(
        workspace_id="local-default",
        conversation_id="conversation-a",
        turn_id="turn-service",
        agent_id="agent-a",
        source="skill",
        action="skill.write_skill",
        resource={"target": "record"},
        risk="high",
        required_role="user",
        payload={"skill_name": "write_skill", "inputs": {}},
        idempotency_key="service-confirmation-test",
    )
    response = client.post(
        f"/v1/permissions/{request.id}/approve",
        headers={**_headers(), "x-actor-kind": "service"},
        json={"expected_version": 0},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "human_confirmation_required"
