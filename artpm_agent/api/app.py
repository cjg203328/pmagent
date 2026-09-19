"""FastAPI application factory for the ArtPM agent gateway.

The factory is intentionally dependency-injected.  A cloud deployment can
provide a remote harness, plugin registry, and tenant-aware stores while the
local default adapter remains available for ``uvicorn`` and development.
"""

from __future__ import annotations

import inspect
import logging
import os
import sqlite3
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

try:  # Keep import errors actionable when the optional API extra is omitted.
    from fastapi import FastAPI, Request, Response
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import ValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException
except ImportError as error:  # pragma: no cover - exercised in minimal installs
    raise RuntimeError(
        "REST gateway requires FastAPI. Install the `api` extra or `fastapi`."
    ) from error

from artpm_agent.security.permission_store import (
    PermissionBindingError,
    PermissionConflictError,
    PermissionNotFoundError,
    PermissionStoreError,
    PermissionValidationError,
)
from artpm_agent.tenancy import TenantContext
from artpm_agent.voice import VoiceSessionBroker
from artpm_agent.workflows.store import WorkflowConflictError

from .embed import EmbedError, EmbedGateway, _origin
from .errors import GatewayError
from .routers import (
    create_capabilities_router,
    create_chat_router,
    create_permissions_router,
    create_search_router,
    create_system_router,
    create_voice_router,
    create_workflows_router,
    create_workspaces_router,
)
from .routers.embed import create_embed_router
from .services import (
    GatewayServices,
    IdentityError,
    RequestPrincipal,
    TrustedHeaderIdentityResolver,
    build_default_services,
    validate_identifier,
)
from .serializers import (
    json_safe as _json_safe,
    model_json as _model_json,
    safe_validation_errors as _safe_validation_errors,
)

logger = logging.getLogger(__name__)
API_VERSION = "v1"


def _cors_origins() -> list[str]:
    """Return an explicit CORS allowlist for browser-to-gateway deployments."""
    configured = os.getenv("ARTPM_CORS_ORIGINS", "")
    raw_origins = [item.strip() for item in configured.split(",") if item.strip()]
    if not raw_origins:
        return ["http://127.0.0.1:8501", "http://localhost:8501"]
    if "*" in raw_origins:
        raise RuntimeError(
            "ARTPM_CORS_ORIGINS must list explicit origins; '*' is not allowed"
        )

    origins: list[str] = []
    for value in raw_origins:
        try:
            # Browser Origin headers omit default ports and never contain a
            # trailing slash. Reuse the embed boundary's canonical parser so
            # CORS and embed allowlists accept the same equivalent forms.
            normalized = _origin(value)
        except EmbedError as error:
            raise RuntimeError(
                "ARTPM_CORS_ORIGINS must contain valid http(s) origins"
            ) from error
        if normalized not in origins:
            origins.append(normalized)
    return origins


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    if isinstance(value, str) and value:
        return value
    value = uuid4().hex
    request.state.request_id = value
    return value


def _error_payload(request: Request, error: GatewayError) -> dict[str, Any]:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "request_id": _request_id(request),
        }
    }


def _principal(request: Request) -> RequestPrincipal:
    resolver = request.app.state.identity_resolver
    try:
        value = resolver(request)
        if inspect.isawaitable(value):
            raise IdentityError(
                "async identity resolvers are not supported by this route"
            )
    except (IdentityError, ValueError) as error:
        raise GatewayError(401, "invalid_identity", str(error)) from error
    if not isinstance(value, RequestPrincipal):
        raise GatewayError(
            500,
            "identity_contract_error",
            "identity resolver returned an invalid principal",
        )
    try:
        request.state.tenant_context = value.tenant_context(
            request_id=_request_id(request)
        )
    except Exception as error:  # noqa: BLE001 - fail closed on tenancy contract errors
        raise GatewayError(
            401, "invalid_tenant_context", "trusted tenant context is invalid"
        ) from error
    return value


def _services(request: Request) -> GatewayServices:
    return cast(GatewayServices, request.app.state.gateway_services)


def _tenant_context(request: Request) -> TenantContext:
    context = getattr(request.state, "tenant_context", None)
    if context is None:
        # All authenticated routes call _principal first.  Keep this helper
        # fail-closed if a future route forgets that dependency.
        raise GatewayError(
            500, "tenant_context_missing", "tenant context was not initialized"
        )
    if not isinstance(context, TenantContext):
        raise GatewayError(
            500,
            "tenant_context_invalid",
            "tenant context has an invalid type",
        )
    return context


def _human(principal: RequestPrincipal) -> None:
    if principal.actor_kind != "human":
        raise GatewayError(
            403, "human_confirmation_required", "approval requires a human actor"
        )


def _require_workspace(
    services: GatewayServices,
    principal: RequestPrincipal,
    request: Request | None = None,
) -> dict[str, Any]:
    try:
        if request is not None:
            _tenant_context(request).require_workspace(principal.workspace_id)
        workspace = services.conversations.get_workspace(principal.workspace_id)
    except Exception as error:  # noqa: BLE001 - normalize store errors
        raise GatewayError(
            503, "workspace_store_unavailable", "workspace store is unavailable"
        ) from error
    if workspace is None:
        raise GatewayError(404, "workspace_not_found", "workspace was not found")
    bind_tenant = getattr(services.conversations, "ensure_workspace_tenant", None)
    if callable(bind_tenant):
        try:
            if not bind_tenant(principal.workspace_id, principal.tenant_id):
                raise GatewayError(
                    403,
                    "workspace_tenant_mismatch",
                    "workspace is not assigned to this tenant",
                )
        except GatewayError:
            raise
        except Exception as error:  # noqa: BLE001 - fail closed on scope errors
            raise GatewayError(
                503,
                "workspace_scope_unavailable",
                "workspace scope could not be verified",
            ) from error
    if not isinstance(workspace, Mapping):
        raise GatewayError(
            500,
            "workspace_contract_error",
            "workspace store returned an invalid record",
        )
    return dict(workspace)


def _conversation(
    services: GatewayServices,
    principal: RequestPrincipal,
    conversation_id: str,
) -> dict[str, Any]:
    try:
        conversation_id = validate_identifier(
            conversation_id, "conversation_id", max_length=256
        )
        conversation = services.conversations.get_conversation(
            conversation_id, workspace_id=principal.workspace_id
        )
    except (ValueError, IdentityError) as error:
        raise GatewayError(400, "invalid_conversation_id", str(error)) from error
    if conversation is None:
        # Return the same 404 for another tenant as for an unknown ID.  This
        # prevents the endpoint from becoming a cross-tenant existence oracle.
        raise GatewayError(404, "conversation_not_found", "conversation was not found")
    if not isinstance(conversation, Mapping):
        raise GatewayError(
            500,
            "conversation_contract_error",
            "conversation store returned an invalid record",
        )
    return dict(conversation)


def _map_store_error(error: Exception) -> GatewayError:
    if isinstance(error, (PermissionConflictError, WorkflowConflictError)):
        return GatewayError(409, "state_conflict", str(error))
    if isinstance(error, PermissionBindingError):
        return GatewayError(409, "permission_binding_conflict", str(error))
    if isinstance(error, PermissionNotFoundError):
        return GatewayError(
            404, "permission_not_found", "permission request was not found"
        )
    if isinstance(error, PermissionValidationError):
        return GatewayError(422, "invalid_permission_request", str(error))
    if isinstance(error, PermissionStoreError):
        return GatewayError(
            503, "permission_store_error", "permission store is unavailable"
        )
    if isinstance(error, sqlite3.Error):
        return GatewayError(
            503, "storage_unavailable", "persistent storage is unavailable"
        )
    if isinstance(error, KeyError):
        return GatewayError(404, "resource_not_found", str(error).strip("'"))
    if isinstance(error, (ValueError, ValidationError)):
        return GatewayError(422, "invalid_request", str(error))
    if isinstance(error, PermissionError):
        return GatewayError(403, "operation_not_allowed", str(error))
    return GatewayError(
        500, "internal_error", "the gateway could not complete the request"
    )


def create_app(
    services: GatewayServices | None = None,
    *,
    identity_resolver: Callable[[Request], RequestPrincipal] | None = None,
    gateway_secret: str | None = None,
    require_gateway_secret: bool | None = None,
    voice_broker: Any | None = None,
) -> FastAPI:
    """Create an isolated FastAPI application instance.

    Tests and cloud hosts should inject ``GatewayServices`` and, where needed,
    an identity dependency.  The no-argument form uses the lazy local runtime.
    """

    services = services or build_default_services()
    if require_gateway_secret is None:
        environment = (
            (os.getenv("ARTPM_ENV") or os.getenv("ENV") or "development")
            .strip()
            .lower()
        )
        require_gateway_secret = environment in {"prod", "production"}
    if identity_resolver is None:
        default_resolver = TrustedHeaderIdentityResolver(
            gateway_secret,
            require_secret=require_gateway_secret,
        )
        if require_gateway_secret and not default_resolver.gateway_secret:
            raise RuntimeError("ARTPM_GATEWAY_SHARED_SECRET is required in production")
        resolver: Callable[[Request], RequestPrincipal] = default_resolver
    else:
        resolver = identity_resolver

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            services.close()

    app = FastAPI(
        title="ArtPM Agent API",
        version=API_VERSION,
        description="Tenant-scoped gateway for chat, capabilities, permissions, and workflows.",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    from artpm_agent.observability import instrument_fastapi

    instrument_fastapi(app)
    try:
        embed_gateway = EmbedGateway()
    except EmbedError as error:
        # Keep the core gateway available while exposing a deterministic
        # configuration error through the opt-in embed routes.
        logger.error("Embed configuration is invalid: %s", error)
        embed_gateway = EmbedGateway.__new__(EmbedGateway)
        setattr(embed_gateway, "settings", None)
    cors_origins = _cors_origins()
    settings = getattr(embed_gateway, "settings", None)
    channels = getattr(settings, "channels", {}) if settings is not None else {}
    for channel in channels.values():
        for origin in getattr(channel, "allowed_origins", ()):
            if origin not in cors_origins:
                cors_origins.append(origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    app.state.gateway_services = services
    app.state.identity_resolver = resolver
    app.state.voice_broker = voice_broker or VoiceSessionBroker()
    app.state.embed_gateway = embed_gateway

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[..., Any],
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        try:
            request.state.request_id = validate_identifier(
                incoming, "request_id", max_length=128
            )
        except IdentityError:
            request.state.request_id = uuid4().hex
        response = cast(Response, await call_next(request))
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(
        request: Request,
        error: GatewayError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code, content=_error_payload(request, error)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        safe_errors = _safe_validation_errors(error.errors())
        gateway_error = GatewayError(
            422, "validation_error", "request validation failed"
        )
        payload = _error_payload(request, gateway_error)
        payload["error"]["fields"] = safe_errors
        return JSONResponse(status_code=422, content=payload)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        status_code = int(error.status_code)
        code = {
            404: "route_not_found",
            405: "method_not_allowed",
        }.get(status_code, "http_error")
        message = {
            404: "requested API route was not found",
            405: "HTTP method is not allowed for this route",
        }.get(status_code, "the API request could not be completed")
        gateway_error = GatewayError(status_code, code, message)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, gateway_error),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        logger.exception(
            "Unhandled API error [%s]", _request_id(request), exc_info=error
        )
        gateway_error = GatewayError(
            500, "internal_error", "the gateway could not complete the request"
        )
        return JSONResponse(
            status_code=500, content=_error_payload(request, gateway_error)
        )

    app.include_router(
        create_system_router(
            services=services,
            api_version=API_VERSION,
            request_id=_request_id,
            normalize_json=_json_safe,
            voice_broker=app.state.voice_broker,
            logger=logger,
        )
    )
    app.include_router(
        create_capabilities_router(
            services=services,
            principal_for_request=_principal,
            require_workspace=_require_workspace,
            normalize_json=_json_safe,
        )
    )
    app.include_router(
        create_permissions_router(
            services=services,
            principal_for_request=_principal,
            require_workspace=_require_workspace,
            tenant_context=_tenant_context,
            map_store_error=_map_store_error,
        )
    )
    app.include_router(
        create_workflows_router(
            services=services,
            principal_for_request=_principal,
            require_workspace=_require_workspace,
            require_conversation=_conversation,
            tenant_context=_tenant_context,
            map_store_error=_map_store_error,
            normalize_json=_model_json,
        )
    )

    def _require_workspace_only(
        services: GatewayServices,
        principal: RequestPrincipal,
        request: Request,
    ) -> None:
        """Adapter for routers that only need workspace existence check."""
        _require_workspace(services, principal, request)

    def _require_conversation_optional(
        services: GatewayServices,
        principal: RequestPrincipal,
        conversation_id: str | None,
    ) -> Mapping[str, Any]:
        """Adapter for routers that accept optional conversation_id."""
        if conversation_id is None:
            raise GatewayError(
                400, "conversation_id_required", "conversation_id is required"
            )
        return _conversation(services, principal, conversation_id)

    app.include_router(
        create_workspaces_router(
            services=services,
            principal_for_request=_principal,
            require_workspace=_require_workspace_only,
            request_id=_request_id,
            normalize_json=_json_safe,
        )
    )
    app.include_router(
        create_search_router(
            services=services,
            principal_for_request=_principal,
            require_workspace=_require_workspace_only,
            request_id=_request_id,
            normalize_json=_json_safe,
        )
    )
    app.include_router(
        create_voice_router(
            services=services,
            principal_for_request=_principal,
            require_workspace=_require_workspace_only,
            require_human=_human,
            require_conversation=_require_conversation_optional,
            request_id=_request_id,
            normalize_json=_json_safe,
            voice_broker=app.state.voice_broker,
        )
    )
    app.include_router(
        create_chat_router(
            services=services,
            principal_for_request=_principal,
            require_workspace=_require_workspace_only,
            require_conversation=_require_conversation_optional,
            tenant_context=_tenant_context,
            map_store_error=_map_store_error,
            request_id=_request_id,
            normalize_json=_json_safe,
        )
    )

    app.include_router(
        create_embed_router(
            services=services,
            principal_for_request=_principal,
            require_workspace=_require_workspace_only,
            require_conversation=_conversation,
            tenant_context=_tenant_context,
            request_id=_request_id,
            normalize_json=_json_safe,
            embed_gateway=embed_gateway,
        )
    )

    return app


__all__ = ["API_VERSION", "GatewayError", "create_app"]
