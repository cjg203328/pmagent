"""Capability catalog routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from fastapi import APIRouter, Request

from artpm_agent.api.errors import GatewayError
from artpm_agent.api.services import GatewayServices, RequestPrincipal


def create_capabilities_router(
    *,
    services: GatewayServices,
    principal_for_request: Callable[[Request], RequestPrincipal],
    require_workspace: Callable[[GatewayServices, RequestPrincipal, Request], object],
    normalize_json: Callable[[object], object],
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/capabilities", tags=["capabilities"])
    def capabilities(request: Request) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        try:
            capabilities_value = services.capability_provider()
        except Exception as error:  # noqa: BLE001 - normalize provider errors
            raise GatewayError(
                503,
                "capabilities_unavailable",
                "capability registry is unavailable",
            ) from error
        if isinstance(capabilities_value, Mapping):
            entries: list[object] = []
            for name, value in capabilities_value.items():
                normalized = normalize_json(value)
                details = (
                    dict(normalized)
                    if isinstance(normalized, Mapping)
                    else {"description": str(value)}
                )
                entries.append({"name": str(name), **details})
        else:
            entries = [
                normalize_json(item)
                for item in (capabilities_value or [])
            ]
        deployment: object = []
        provider = services.deployment_capability_provider
        if provider is not None:
            try:
                deployment = normalize_json(provider())
            except Exception:  # noqa: BLE001 - diagnostics must not hide skills
                deployment = []
        return {
            "workspace_id": principal.workspace_id,
            "items": entries,
            "deployment": deployment,
        }

    return router


__all__ = ["create_capabilities_router"]
