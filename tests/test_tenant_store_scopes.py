"""SQL-level workspace filtering for persistent tenant-facing stores."""

from __future__ import annotations

import pytest

from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore
from artpm_agent.security.permission_store import (
    PermissionNotFoundError,
    PermissionStore,
)
from artpm_agent.tenancy import TenantContext, WorkspaceStoreGuard
from artpm_agent.workflows.defaults import get_builtin_workflows
from artpm_agent.workflows.store import WorkflowConflictError, WorkflowStore


def _context(workspace_id: str) -> TenantContext:
    return TenantContext(
        tenant_id="tenant-a",
        workspace_id=workspace_id,
        principal_id="user-a",
    )


def test_knowledge_resource_id_cannot_cross_workspace_scope(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db", enable_vector_search=False)
    resource = store.ingest_resource(
        title="Scoped resource",
        searchable_text="workspace A only",
        workspace_id="workspace-a",
        source_id="scoped-resource",
    )

    assert store.get_resource(resource["id"], workspace_id="workspace-b") is None
    assert store.list_versions(resource["id"], workspace_id="workspace-b") == []
    assert store.archive_resource(resource["id"], workspace_id="workspace-b") is False
    scoped = WorkspaceStoreGuard(store, _context("workspace-a"))
    assert scoped.call("get_resource", resource["id"])["workspace_id"] == "workspace-a"


def test_permission_transition_rejects_request_id_from_other_workspace(tmp_path):
    store = PermissionStore(tmp_path / "permissions.db")
    request = store.create_request(
        workspace_id="workspace-a",
        conversation_id="conversation-a",
        turn_id="turn-a",
        agent_id="agent-a",
        source="skill",
        action="skill.external",
        resource={"id": "resource-a"},
        risk="medium",
        required_role="user",
        payload={"value": 1},
        idempotency_key="permission-a",
    )

    assert store.get(request.id, workspace_id="workspace-b") is None
    with pytest.raises(PermissionNotFoundError):
        store.decide(
            request.id,
            decision="approved",
            actor_id="user-b",
            actor_role="user",
            expected_version=request.state_version,
            workspace_id="workspace-b",
        )
    approved = WorkspaceStoreGuard(store, _context("workspace-a")).call(
        "decide",
        request.id,
        decision="approved",
        actor_id="user-a",
        actor_role="user",
        expected_version=request.state_version,
    )
    assert approved.status == "approved"


def test_workflow_run_children_are_hidden_from_other_workspace(tmp_path):
    db_path = tmp_path / "workflow.db"
    conversations = ConversationStore(db_path)
    conversation = conversations.create_conversation("Scoped workflow")
    store = WorkflowStore(db_path)
    definition = store.get_definition("quote_assessment")
    run = store.create_run(
        definition,
        conversation["id"],
        input_data={"quote_amount": 100, "cost": 80},
        idempotency_key="scope-test",
    )

    assert store.get_run(run.id, workspace_id="workspace-b") is None
    assert store.get_step(run.id, 0, workspace_id="workspace-b") is None
    assert store.list_steps(run.id, workspace_id="workspace-b") == []
    assert store.list_events(run.id, workspace_id="workspace-b") == []
    assert store.list_approvals(run.id, workspace_id="workspace-b") == []
    scoped = WorkspaceStoreGuard(store, TenantContext.local())
    assert scoped.call("get_run", run.id).workspace_id == "local-default"


def test_workflow_mutations_are_bound_to_the_request_tenant(tmp_path):
    db_path = tmp_path / "workflow-mutations.db"
    conversations = ConversationStore(db_path)
    now = conversations._utc_now()  # noqa: SLF001
    with conversations._connection(write=True) as connection:  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO workspaces(
                id, profile_id, name, tenant_id, settings_json, created_at, updated_at
            ) VALUES ('workspace-a', 'local-default', 'Workspace A', 'tenant-a', '{}', ?, ?)
            """,
            (now, now),
        )
    conversation = conversations.create_conversation(
        "Scoped workflow", workspace_id="workspace-a"
    )
    store = WorkflowStore(db_path, install_builtins=False)
    definition = get_builtin_workflows()[0].model_copy(
        update={"tenant_id": "tenant-a", "workspace_id": "workspace-a"}
    )
    store.put_definition(definition)
    run = store.create_run(
        definition,
        conversation["id"],
        input_data={"quote_amount": 100, "cost": 80},
        idempotency_key="tenant-mutation-test",
    )

    with pytest.raises(WorkflowConflictError):
        store.transition_run(
            run.id,
            expected_status="pending",
            expected_version=0,
            new_status="cancelled",
            tenant_id="tenant-b",
        )
    with pytest.raises(KeyError):
        store.claim_step(
            run.id,
            0,
            {"quote_amount": 100},
            tenant_id="tenant-b",
        )
    with pytest.raises(KeyError):
        store.request_approval(
            run.id,
            0,
            "user",
            tenant_id="tenant-b",
        )
    with pytest.raises(KeyError):
        store.cancel_run(
            run.id,
            expected_version=run.state_version,
            tenant_id="tenant-b",
        )

    claimed_run, _ = store.claim_step(
        run.id,
        0,
        {"quote_amount": 100, "cost": 80},
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    with pytest.raises(KeyError):
        store.complete_step(
            run.id,
            0,
            "quote",
            {"success": True},
            tenant_id="tenant-b",
        )
    with pytest.raises(KeyError):
        store.fail_step(
            run.id,
            0,
            "should not mutate",
            tenant_id="tenant-b",
        )

    current = store.get_run(
        run.id,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    assert current is not None
    assert current.status == claimed_run.status == "running"
    assert current.state_version == claimed_run.state_version
    assert store.list_events(
        run.id,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )[-1].event_type == "step.started"
