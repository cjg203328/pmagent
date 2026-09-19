"""Workspace management routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..errors import GatewayError
from ..models import WorkspaceCreateRequest
from ..services import GatewayServices, RequestPrincipal, validate_identifier

if TYPE_CHECKING:
    from collections.abc import Callable


def create_workspaces_router(
    *,
    services: GatewayServices,
    principal_for_request: Callable[[Request], RequestPrincipal],
    require_workspace: Callable[[GatewayServices, RequestPrincipal, Request], None],
    request_id: Callable[[Request], str],
    normalize_json: Callable[[Any], Any],
) -> APIRouter:
    """Create workspace management router with injected dependencies."""
    router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])
    @router.get("")
    def list_workspaces(request: Request) -> dict[str, object]:
        """List workspaces visible to the authenticated tenant."""
        principal = principal_for_request(request)
        try:
            # A tenant may own workspaces backed by different agent profiles.
            # Push the ownership predicate into the store before pagination;
            # otherwise a busy database can fill the first page with another
            # tenant's rows and hide valid workspaces from this response.
            list_workspaces = services.conversations.list_workspaces
            try:
                items = list_workspaces(
                    profile_id=None,
                    tenant_id=principal.tenant_id,
                    limit=100,
                )
            except TypeError:
                # Preserve compatibility with injected legacy stores that do
                # not yet expose the optional tenant_id keyword. The final
                # in-memory check remains a defense-in-depth boundary.
                items = list_workspaces(profile_id=None, limit=100)
            visible = [
                item
                for item in items
                # Unbound legacy rows are intentionally hidden until a
                # workspace-scoped request claims them through the normal
                # tenant guard. Never expose them from a list endpoint.
                if item.get("tenant_id") == principal.tenant_id
            ]
        except Exception as error:  # noqa: BLE001 - normalize store errors
            raise GatewayError(
                503, "workspace_store_unavailable", "workspace store is unavailable"
            ) from error
        return {
            "tenant_id": principal.tenant_id,
            "items": [normalize_json(item) for item in visible],
        }

    @router.post("")
    def create_workspace(
        request: Request,
        payload: WorkspaceCreateRequest,
    ) -> JSONResponse:
        """Create one workspace owned by the trusted tenant context."""
        principal = principal_for_request(request)
        if principal.actor_role != "admin":
            raise GatewayError(
                403,
                "workspace_admin_required",
                "workspace creation requires admin role",
            )
        try:
            workspace_id = validate_identifier(payload.id, "workspace_id")
            profile_id = validate_identifier(payload.profile_id, "profile_id")
            item = services.conversations.create_workspace(
                workspace_id,
                payload.name,
                tenant_id=principal.tenant_id,
                profile_id=profile_id,
                settings=payload.settings,
            )
        except ValueError as error:
            raise GatewayError(400, "invalid_workspace", str(error)) from error
        except Exception as error:  # noqa: BLE001 - do not leak sqlite details
            if "unique" in str(error).lower() or "constraint" in str(error).lower():
                raise GatewayError(
                    409, "workspace_exists", "workspace already exists"
                ) from error
            raise GatewayError(
                503, "workspace_store_unavailable", "workspace could not be created"
            ) from error
        return JSONResponse(
            status_code=201,
            content=normalize_json(item),
            headers={"x-request-id": request_id(request)},
        )

    return router
