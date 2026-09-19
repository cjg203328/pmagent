"""Tenant-scoped workflow definition and run routes."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, Query, Request

from artpm_agent.api.errors import GatewayError
from artpm_agent.api.models import (
    WorkflowApprovalRequest,
    WorkflowDefinitionRequest,
    WorkflowRunRequest,
)
from artpm_agent.api.services import (
    GatewayServiceError,
    GatewayServices,
    IdentityError,
    RequestPrincipal,
    validate_identifier,
)
from artpm_agent.workflows.designer import parse_input_map
from artpm_agent.workflows.models import (
    ApprovalRequirement,
    WorkflowDefinition,
    WorkflowStepDefinition,
    WorkflowTrigger,
)
from artpm_agent.workflows.risk_policy import (
    DEFAULT_RISK_POLICY,
    DEFAULT_SKILL_CAPABILITIES,
)

PrincipalResolver = Callable[[Request], RequestPrincipal]
WorkspaceGuard = Callable[
    [GatewayServices, RequestPrincipal, Request | None],
    Mapping[str, Any],
]
ConversationGuard = Callable[
    [GatewayServices, RequestPrincipal, str],
    Mapping[str, Any],
]
TenantContextResolver = Callable[[Request], object]
StoreErrorMapper = Callable[[Exception], GatewayError]
JsonNormalizer = Callable[[Any], Any]


def _human(principal: RequestPrincipal) -> None:
    if principal.actor_kind != "human":
        raise GatewayError(
            403,
            "human_confirmation_required",
            "approval requires a human actor",
        )


def _serialize_workflow_result(
    result: Any,
    normalize_json: JsonNormalizer,
) -> dict[str, object]:
    return {
        "run": normalize_json(result.run),
        "steps": [normalize_json(step) for step in result.steps],
        "approval": (
            normalize_json(result.approval) if result.approval is not None else None
        ),
    }


def _workflow_run_for_principal(
    services: GatewayServices,
    principal: RequestPrincipal,
    run_id: str,
    map_store_error: StoreErrorMapper,
) -> Any:
    try:
        run_id = validate_identifier(run_id, "run_id", max_length=256)
    except IdentityError as error:
        raise GatewayError(400, "invalid_run_id", str(error)) from error
    try:
        run = services.workflows.get_run(
            run_id,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
        )
    except Exception as error:
        raise map_store_error(error) from error
    if run is None:
        raise GatewayError(
            404,
            "workflow_run_not_found",
            "workflow run was not found",
        )
    return run


def _workflow_definition_from_request(
    payload: WorkflowDefinitionRequest,
    principal: RequestPrincipal,
    services: GatewayServices,
) -> WorkflowDefinition:
    raw = payload.model_dump(mode="python")
    capability_allowlist = DEFAULT_SKILL_CAPABILITIES
    if services.workflow_capability_provider is not None:
        provided = services.workflow_capability_provider()
        if not isinstance(provided, Mapping):
            raise GatewayServiceError(
                "workflow capability provider returned an invalid allowlist"
            )
        capability_allowlist = provided
    trigger_raw = cast(dict[str, Any], raw.pop("trigger"))
    trigger = WorkflowTrigger.model_validate(
        {
            **trigger_raw,
            "keywords": tuple(trigger_raw.get("keywords", [])),
            "required_context_keys": tuple(
                trigger_raw.get("required_context_keys", [])
            ),
            "attachment_extensions": tuple(
                trigger_raw.get("attachment_extensions", [])
            ),
            "project_statuses": tuple(trigger_raw.get("project_statuses", [])),
        }
    )
    requested_steps = cast(list[dict[str, Any]], raw.pop("steps"))
    raw.pop("version", None)
    steps: list[WorkflowStepDefinition] = []
    for item in requested_steps:
        skill_id = str(item.get("skill_id") or "").strip()
        capability = str(item.get("capability") or "").strip()
        allowed = capability_allowlist.get(skill_id, frozenset())
        if capability not in allowed:
            raise GatewayError(
                403,
                "workflow_capability_not_allowed",
                "skill/capability is outside the server allowlist: "
                f"{skill_id}:{capability}",
            )
        rule = DEFAULT_RISK_POLICY.resolve(capability)
        if not rule.allowed:
            raise GatewayError(
                403,
                "workflow_capability_blocked",
                f"capability is blocked by server policy: {capability}",
            )
        input_map = parse_input_map(item.get("input_map"))
        if len(json.dumps(input_map, ensure_ascii=False).encode("utf-8")) > 16 * 1024:
            raise ValueError("workflow step input_map exceeds 16 KiB")
        steps.append(
            WorkflowStepDefinition.model_validate(
                {
                    **item,
                    "input_map": input_map,
                    "side_effect": rule.side_effect,
                    "approval": rule.minimum_approval,
                    "on_error": "stop",
                }
            )
        )
    step_tuple = tuple(steps)
    latest = services.workflows.get_definition(
        str(raw.get("id") or ""),
        workspace_id=principal.workspace_id,
        profile_id=principal.profile_id,
        tenant_id=principal.tenant_id,
    )
    if latest is not None and latest.source == "builtin":
        raise PermissionError("built-in workflows cannot be replaced")
    next_version = (latest.version + 1) if latest is not None else 1
    return WorkflowDefinition.model_validate(
        {
            **raw,
            "version": next_version,
            "trigger": trigger,
            "steps": step_tuple,
            "workspace_id": principal.workspace_id,
            "profile_id": principal.profile_id,
            "tenant_id": principal.tenant_id,
            "source": "custom",
            "read_only": all(not step.side_effect for step in step_tuple),
        }
    )


def create_workflows_router(
    *,
    services: GatewayServices,
    principal_for_request: PrincipalResolver,
    require_workspace: WorkspaceGuard,
    require_conversation: ConversationGuard,
    tenant_context: TenantContextResolver,
    map_store_error: StoreErrorMapper,
    normalize_json: JsonNormalizer,
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/workflows", tags=["workflows"])
    def list_workflows(
        request: Request,
        enabled_only: bool = Query(default=False),
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        try:
            definitions = services.workflows.list_definitions(
                workspace_id=principal.workspace_id,
                profile_id=principal.profile_id,
                tenant_id=principal.tenant_id,
                enabled_only=enabled_only,
            )
        except Exception as error:
            raise map_store_error(error) from error
        return {"items": [normalize_json(item) for item in definitions]}

    @router.post("/v1/workflows", tags=["workflows"])
    def create_workflow(
        request: Request,
        payload: WorkflowDefinitionRequest,
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        _human(principal)
        if principal.actor_role != "admin":
            raise GatewayError(
                403,
                "admin_required",
                "creating a workflow requires an admin actor",
            )
        require_workspace(services, principal, request)
        try:
            definition = _workflow_definition_from_request(
                payload,
                principal,
                services,
            )
            stored = services.workflows.put_definition(definition)
        except GatewayError:
            raise
        except Exception as error:
            raise map_store_error(error) from error
        return {"item": normalize_json(stored)}

    @router.get("/v1/workflows/{workflow_id}", tags=["workflows"])
    def get_workflow(
        request: Request,
        workflow_id: str,
        version: int | None = Query(default=None, ge=1),
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        try:
            workflow_id = validate_identifier(
                workflow_id,
                "workflow_id",
                max_length=128,
            )
            definition = services.workflows.get_definition(
                workflow_id,
                version=version,
                workspace_id=principal.workspace_id,
                profile_id=principal.profile_id,
                tenant_id=principal.tenant_id,
            )
        except Exception as error:
            raise map_store_error(error) from error
        if definition is None:
            raise GatewayError(404, "workflow_not_found", "workflow was not found")
        return {"item": normalize_json(definition)}

    @router.post("/v1/workflows/{workflow_id}/runs", tags=["workflows"])
    def run_workflow(
        request: Request,
        workflow_id: str,
        payload: WorkflowRunRequest,
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        require_conversation(
            services,
            principal,
            payload.conversation_id,
        )
        try:
            workflow_id = validate_identifier(
                workflow_id,
                "workflow_id",
                max_length=128,
            )
            definition = services.workflows.get_definition(
                workflow_id,
                version=payload.version,
                workspace_id=principal.workspace_id,
                profile_id=principal.profile_id,
                tenant_id=principal.tenant_id,
            )
            if definition is None:
                raise GatewayError(
                    404,
                    "workflow_not_found",
                    "workflow was not found",
                )
            engine = services.get_workflow_engine(
                tenant_context(request),
                profile_id=principal.profile_id,
            )
            result = engine.start(
                definition,
                payload.conversation_id,
                input_data=dict(payload.input_data),
                context_data=dict(payload.context_data),
                turn_id=payload.turn_id or uuid4().hex,
                idempotency_key=payload.idempotency_key,
                auto_resume=payload.auto_resume,
            )
        except GatewayError:
            raise
        except Exception as error:
            raise map_store_error(error) from error
        return _serialize_workflow_result(result, normalize_json)

    @router.get("/v1/workflow-runs", tags=["workflows"])
    def list_workflow_runs(
        request: Request,
        conversation_id: str = Query(..., min_length=1, max_length=256),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        require_conversation(services, principal, conversation_id)
        try:
            runs = services.workflows.list_runs(
                conversation_id,
                workspace_id=principal.workspace_id,
                profile_id=principal.profile_id,
                tenant_id=principal.tenant_id,
                limit=limit,
            )
        except Exception as error:
            raise map_store_error(error) from error
        return {"items": [normalize_json(run) for run in runs]}

    @router.get("/v1/workflow-runs/{run_id}", tags=["workflows"])
    def get_workflow_run(request: Request, run_id: str) -> dict[str, object]:
        principal = principal_for_request(request)
        _workflow_run_for_principal(
            services,
            principal,
            run_id,
            map_store_error,
        )
        try:
            result = services.get_workflow_engine(
                tenant_context(request),
                profile_id=principal.profile_id,
            ).result(run_id, workspace_id=principal.workspace_id)
        except Exception as error:
            raise map_store_error(error) from error
        return _serialize_workflow_result(result, normalize_json)

    def decide_workflow(
        request: Request,
        run_id: str,
        step_index: int,
        payload: WorkflowApprovalRequest,
        decision: Literal["approved", "rejected"],
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        _human(principal)
        _workflow_run_for_principal(
            services,
            principal,
            run_id,
            map_store_error,
        )
        if step_index < 0 or step_index > 7:
            raise GatewayError(
                400,
                "invalid_step_index",
                "step_index is out of range",
            )
        try:
            result = services.get_workflow_engine(
                tenant_context(request),
                profile_id=principal.profile_id,
            ).decide_approval(
                run_id,
                step_index,
                decision=decision,
                actor=principal.actor_id,
                actor_level=cast(ApprovalRequirement, principal.actor_role),
                note=payload.note,
                auto_resume=True,
                workspace_id=principal.workspace_id,
            )
        except Exception as error:
            raise map_store_error(error) from error
        return _serialize_workflow_result(result, normalize_json)

    @router.post(
        "/v1/workflow-runs/{run_id}/steps/{step_index}/approve",
        tags=["workflows"],
    )
    def approve_workflow(
        request: Request,
        run_id: str,
        step_index: int,
        payload: WorkflowApprovalRequest,
    ) -> dict[str, object]:
        return decide_workflow(
            request,
            run_id,
            step_index,
            payload,
            "approved",
        )

    @router.post(
        "/v1/workflow-runs/{run_id}/steps/{step_index}/reject",
        tags=["workflows"],
    )
    def reject_workflow(
        request: Request,
        run_id: str,
        step_index: int,
        payload: WorkflowApprovalRequest,
    ) -> dict[str, object]:
        return decide_workflow(
            request,
            run_id,
            step_index,
            payload,
            "rejected",
        )

    return router


__all__ = ["create_workflows_router"]
