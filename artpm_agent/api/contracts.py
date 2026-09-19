"""Structural ports owned by the public API composition boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Protocol, TypeAlias

from artpm_agent.runtime.events import AgentEvent
from artpm_agent.security.permission_store import (
    PermissionRequest,
    PermissionRisk,
    PermissionRole,
)
from artpm_agent.workflows.models import (
    ApprovalRequirement,
    ApprovalStatus,
    RunStatus,
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowRun,
)


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = Mapping[str, JsonValue]
Record: TypeAlias = Mapping[str, Any]
CapabilityCatalog: TypeAlias = Mapping[str, object] | Sequence[object]


class ConversationStorePort(Protocol):
    @property
    def db_path(self) -> str: ...

    def get_workspace(self, workspace_id: str | None = None) -> dict[str, Any] | None: ...

    def ensure_workspace_tenant(self, workspace_id: str, tenant_id: str) -> bool: ...

    def list_workspaces(
        self,
        *,
        profile_id: str | None = "local-default",
        tenant_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...

    def create_workspace(
        self,
        workspace_id: str,
        name: str,
        *,
        tenant_id: str = "local",
        profile_id: str = "local-default",
        settings: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def create_conversation(
        self,
        title: str = "新对话",
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]: ...

    def get_conversation(
        self,
        conversation_id: str,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any] | None: ...

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        status: str = "complete",
        turn_id: str | None = None,
        model_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]: ...


class PermissionStorePort(Protocol):
    @property
    def db_path(self) -> str: ...

    def get(
        self,
        request_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> PermissionRequest | None: ...

    def list_pending(
        self,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        conversation_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[PermissionRequest]: ...

    def decide(
        self,
        request_id: str,
        *,
        decision: Literal["approved", "rejected"],
        actor_id: str,
        actor_role: PermissionRole,
        expected_version: int,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        acknowledged_risk: PermissionRisk | None = None,
    ) -> PermissionRequest: ...

    def claim_execution(
        self,
        request_id: str,
        *,
        execution_id: str,
        expected_version: int,
        expected_payload_sha256: str | None = None,
        expected_action_sha256: str | None = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> PermissionRequest: ...

    def complete_execution(
        self,
        request_id: str,
        *,
        execution_id: str,
        success: bool,
        expected_version: int,
        result: Any = None,
        error: str | None = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> PermissionRequest: ...


class WorkflowStorePort(Protocol):
    @property
    def db_path(self) -> str: ...

    def put_definition(self, definition: WorkflowDefinition) -> WorkflowDefinition: ...

    def get_definition(
        self,
        workflow_id: str,
        *,
        version: int | None = None,
        workspace_id: str = "local-default",
        profile_id: str = "local-default",
        tenant_id: str | None = None,
    ) -> WorkflowDefinition | None: ...

    def list_definitions(
        self,
        *,
        workspace_id: str = "local-default",
        profile_id: str = "local-default",
        latest_only: bool = True,
        enabled_only: bool = False,
        tenant_id: str | None = None,
    ) -> list[WorkflowDefinition]: ...

    def get_run(
        self,
        run_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> WorkflowRun | None: ...

    def list_runs(
        self,
        conversation_id: str,
        *,
        statuses: tuple[RunStatus, ...] | None = None,
        workspace_id: str = "local-default",
        profile_id: str = "local-default",
        limit: int = 20,
        tenant_id: str | None = None,
    ) -> list[WorkflowRun]: ...


class WorkflowEnginePort(Protocol):
    def start(
        self,
        definition: WorkflowDefinition,
        conversation_id: str,
        *,
        input_data: dict[str, Any] | None = None,
        context_data: dict[str, Any] | None = None,
        turn_id: str | None = None,
        idempotency_key: str | None = None,
        auto_resume: bool = True,
    ) -> WorkflowExecutionResult: ...

    def result(
        self,
        run_id: str,
        *,
        workspace_id: str | None = None,
    ) -> WorkflowExecutionResult: ...

    def decide_approval(
        self,
        run_id: str,
        step_index: int,
        *,
        decision: ApprovalStatus,
        actor: str,
        actor_level: ApprovalRequirement = "user",
        note: str | None = None,
        auto_resume: bool = True,
        workspace_id: str | None = None,
    ) -> WorkflowExecutionResult: ...


class EventBusPort(Protocol):
    def subscribe(
        self,
        handler: Callable[[AgentEvent], None],
    ) -> Callable[[], None]: ...


__all__ = [
    "CapabilityCatalog",
    "ConversationStorePort",
    "EventBusPort",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "PermissionStorePort",
    "Record",
    "WorkflowEnginePort",
    "WorkflowStorePort",
]
