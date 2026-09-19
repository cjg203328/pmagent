"""Tenant-scoped permission request routes."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, Query, Request

from artpm_agent.api.errors import GatewayError
from artpm_agent.api.models import PermissionDecisionRequest
from artpm_agent.api.services import (
    GatewayServiceError,
    GatewayServices,
    IdentityError,
    RequestPrincipal,
    validate_identifier,
)
from artpm_agent.security.permission_store import PermissionRisk, PermissionRole

PrincipalResolver = Callable[[Request], RequestPrincipal]
WorkspaceGuard = Callable[
    [GatewayServices, RequestPrincipal, Request | None],
    Mapping[str, Any],
]
TenantContextResolver = Callable[[Request], object]
StoreErrorMapper = Callable[[Exception], GatewayError]


def _human(principal: RequestPrincipal) -> None:
    if principal.actor_kind != "human":
        raise GatewayError(
            403,
            "human_confirmation_required",
            "approval requires a human actor",
        )


def _permission_for_principal(
    services: GatewayServices,
    principal: RequestPrincipal,
    request_id: str,
    map_store_error: StoreErrorMapper,
) -> Any:
    try:
        item = services.permissions.get(
            validate_identifier(request_id, "request_id", max_length=256),
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
        )
    except (ValueError, IdentityError) as error:
        raise GatewayError(400, "invalid_permission_id", str(error)) from error
    except Exception as error:
        raise map_store_error(error) from error
    if item is None or item.workspace_id != principal.workspace_id:
        raise GatewayError(
            404,
            "permission_not_found",
            "permission request was not found",
        )
    return item


def create_permissions_router(
    *,
    services: GatewayServices,
    principal_for_request: PrincipalResolver,
    require_workspace: WorkspaceGuard,
    tenant_context: TenantContextResolver,
    map_store_error: StoreErrorMapper,
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/permissions", tags=["permissions"])
    def list_permissions(
        request: Request,
        conversation_id: str | None = Query(default=None, max_length=256),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        if conversation_id is not None:
            conversation_id = validate_identifier(
                conversation_id,
                "conversation_id",
                max_length=256,
            )
        try:
            items = services.permissions.list_pending(
                workspace_id=principal.workspace_id,
                tenant_id=principal.tenant_id,
                conversation_id=conversation_id,
                limit=limit,
            )
        except Exception as error:
            raise map_store_error(error) from error
        return {"items": [item.to_dict() for item in items]}

    @router.get("/v1/permissions/{request_id}", tags=["permissions"])
    def get_permission(request: Request, request_id: str) -> dict[str, object]:
        principal = principal_for_request(request)
        item = _permission_for_principal(
            services,
            principal,
            request_id,
            map_store_error,
        )
        return {"item": item.to_dict()}

    def decide_permission(
        request: Request,
        request_id: str,
        payload: PermissionDecisionRequest,
        decision: Literal["approved", "rejected"],
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        _human(principal)
        item = _permission_for_principal(
            services,
            principal,
            request_id,
            map_store_error,
        )
        supported_sources = services.permission_executor_sources
        if (
            decision == "approved"
            and services.permission_executor is not None
            and supported_sources is not None
            and item.source not in supported_sources
        ):
            raise GatewayError(
                422,
                "unsupported_permission_source",
                "this host cannot execute the requested permission source",
            )
        try:
            decided = services.permissions.decide(
                item.id,
                decision=decision,
                actor_id=principal.actor_id,
                actor_role=cast(PermissionRole, principal.actor_role),
                expected_version=payload.expected_version,
                workspace_id=principal.workspace_id,
                tenant_id=principal.tenant_id,
                acknowledged_risk=(
                    cast(PermissionRisk, payload.acknowledged_risk)
                    if decision == "approved"
                    else None
                ),
            )
            executor = services.permission_executor
            if decision == "approved" and executor is not None:
                execution_id = f"gateway-{uuid4().hex}"
                claimed = services.permissions.claim_execution(
                    decided.id,
                    execution_id=execution_id,
                    expected_version=decided.state_version,
                    expected_payload_sha256=decided.payload_sha256,
                    expected_action_sha256=decided.action_sha256,
                    workspace_id=principal.workspace_id,
                    tenant_id=principal.tenant_id,
                )
                try:
                    try:
                        accepts_context = len(inspect.signature(executor).parameters) >= 2
                    except (TypeError, ValueError):
                        accepts_context = False
                    result = (
                        executor(claimed, tenant_context(request))
                        if accepts_context
                        else executor(claimed)
                    )
                    if inspect.isawaitable(result):
                        raise GatewayServiceError(
                            "async permission executors require an async gateway adapter"
                        )
                    completed = services.permissions.complete_execution(
                        claimed.id,
                        execution_id=execution_id,
                        success=True,
                        expected_version=claimed.state_version,
                        result=result,
                        workspace_id=principal.workspace_id,
                        tenant_id=principal.tenant_id,
                    )
                except Exception as error:
                    services.permissions.complete_execution(
                        claimed.id,
                        execution_id=execution_id,
                        success=False,
                        expected_version=claimed.state_version,
                        error=str(error),
                        workspace_id=principal.workspace_id,
                        tenant_id=principal.tenant_id,
                    )
                    raise GatewayError(
                        502,
                        "permission_execution_failed",
                        "approved operation failed",
                    ) from error
                return {"item": completed.to_dict()}
        except GatewayError:
            raise
        except Exception as error:
            raise map_store_error(error) from error
        return {"item": decided.to_dict()}

    @router.post("/v1/permissions/{request_id}/approve", tags=["permissions"])
    def approve_permission(
        request: Request,
        request_id: str,
        payload: PermissionDecisionRequest,
    ) -> dict[str, object]:
        return decide_permission(request, request_id, payload, "approved")

    @router.post("/v1/permissions/{request_id}/reject", tags=["permissions"])
    def reject_permission(
        request: Request,
        request_id: str,
        payload: PermissionDecisionRequest,
    ) -> dict[str, object]:
        return decide_permission(request, request_id, payload, "rejected")

    return router


__all__ = ["create_permissions_router"]
