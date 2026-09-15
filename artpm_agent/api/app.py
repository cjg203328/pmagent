"""FastAPI application factory for the ArtPM agent gateway.

The factory is intentionally dependency-injected.  A cloud deployment can
provide a remote harness, plugin registry, and tenant-aware stores while the
local default adapter remains available for ``uvicorn`` and development.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, is_dataclass
import inspect
import json
import logging
import os
from queue import Empty as QueueEmpty
from queue import Queue
import sqlite3
from typing import Any, Callable
from uuid import uuid4

try:  # Keep import errors actionable when the optional API extra is omitted.
    from fastapi import FastAPI, Query, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, StreamingResponse
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
from artpm_agent.workflows.store import WorkflowConflictError
from artpm_agent.voice import (
    VoiceConfigurationError,
    VoiceSessionBroker,
    VoiceUnavailableError,
)

from .models import (
    ChatRequest,
    EmbedChatRequest,
    EmbedExchangeRequest,
    EmbedSessionRequest,
    ChatStreamRequest,
    KnowledgeSearchRequest,
    PermissionDecisionRequest,
    WorkflowApprovalRequest,
    WorkflowDefinitionRequest,
    WorkflowRunRequest,
    VoiceSessionRequest,
    WorkspaceCreateRequest,
)
from .services import (
    ChatCommand,
    ChatOutcome,
    GatewayServiceError,
    GatewayServices,
    IdentityError,
    RequestPrincipal,
    TrustedHeaderIdentityResolver,
    build_default_services,
    validate_identifier,
)
from .embed import EmbedError, EmbedGateway

logger = logging.getLogger(__name__)
API_VERSION = "v1"


def _cors_origins() -> list[str]:
    """Return an explicit CORS allowlist for browser-to-gateway deployments."""
    configured = os.getenv("ARTPM_CORS_ORIGINS", "")
    origins = [item.strip().rstrip("/") for item in configured.split(",") if item.strip()]
    if origins:
        if "*" in origins:
            raise RuntimeError("ARTPM_CORS_ORIGINS must list explicit origins; '*' is not allowed")
        return origins
    return ["http://127.0.0.1:8501", "http://localhost:8501"]


class GatewayError(Exception):
    """Structured error raised by route handlers."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


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


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """Bound metadata/results before handing them to a JSON response."""

    if depth > 16:
        return "[depth limited]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth + 1) for item in value]
    if is_dataclass(value):
        return _json_safe(asdict(value), depth=depth + 1)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"), depth=depth + 1)
    return str(value)[:4000]


def _safe_validation_errors(errors: Any) -> list[dict[str, Any]]:
    """Keep validation responses actionable without echoing request values."""
    safe: list[dict[str, Any]] = []
    for item in list(errors or [])[:20]:
        if not isinstance(item, Mapping):
            continue
        location = [str(part)[:80] for part in list(item.get("loc", ()))[:8]]
        safe.append(
            {
                "loc": location,
                "type": str(item.get("type", "validation_error"))[:120],
                "message": "request field failed validation",
            }
        )
    return safe


def _model_json(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return _json_safe(value)


def _coerce_chat_outcome(value: Any) -> ChatOutcome | None:
    """Normalize an optional stream result without accepting arbitrary fields."""

    if isinstance(value, ChatOutcome):
        return value
    if not isinstance(value, Mapping) or "type" in value or "event" in value:
        return None
    if not any(key in value for key in ("response", "success", "error")):
        return None
    allowed = {
        "response",
        "success",
        "awaiting_approval",
        "handled_by",
        "metadata",
        "artifacts",
        "error",
    }
    try:
        return ChatOutcome(**{key: value[key] for key in allowed if key in value})
    except (TypeError, ValueError):
        return None


def _stream_event_payload(value: Any, *, run_id: str, turn_id: str) -> dict[str, Any] | None:
    """Turn an AgentEvent or a small provider mapping into public JSON."""

    if isinstance(value, str):
        raw: dict[str, Any] = {"type": "message_update", "delta": value}
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            raw = value.to_dict()
        except Exception:  # pragma: no cover - defensive provider boundary
            return None
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        return None
    raw_event_type = raw.get("type") or raw.get("event") or ""
    event_type = str(getattr(raw_event_type, "value", raw_event_type)).strip()
    if not event_type:
        if "delta" in raw or "content" in raw:
            event_type = "message_update"
        else:
            return None
    payload = _json_safe(raw)
    if not isinstance(payload, dict):
        return None
    payload["type"] = event_type
    payload.setdefault("run_id", run_id)
    payload.setdefault("turn_id", turn_id)
    # Provider exception text is never a client contract. Keep the event
    # useful for rendering while exposing only a stable failure marker.
    if payload.get("is_error") or event_type in {"runtime_error", "error"}:
        payload["is_error"] = True
        payload["error"] = "chat_failed"
        payload.pop("exception", None)
        payload["metadata"] = {"error_code": "chat_failed"}
    return payload


def _sse_frame(payload: Mapping[str, Any], *, event_id: str) -> str:
    """Encode one compact SSE frame with JSON data and a resumable id."""

    event_type = str(payload.get("type") or "message_update")
    data = json.dumps(_json_safe(payload), ensure_ascii=False, separators=(",", ":"))
    # SSE data may contain newlines; each line must carry its own data prefix.
    body = "".join(f"data: {line}\n" for line in data.splitlines() or [""])
    return f"id: {event_id}\nevent: {event_type}\n{body}\n"


def _principal(request: Request) -> RequestPrincipal:
    resolver = request.app.state.identity_resolver
    try:
        value = resolver(request)
        if inspect.isawaitable(value):
            raise IdentityError("async identity resolvers are not supported by this route")
    except (IdentityError, ValueError) as error:
        raise GatewayError(401, "invalid_identity", str(error)) from error
    if not isinstance(value, RequestPrincipal):
        raise GatewayError(500, "identity_contract_error", "identity resolver returned an invalid principal")
    try:
        request.state.tenant_context = value.tenant_context(
            request_id=_request_id(request)
        )
    except Exception as error:  # noqa: BLE001 - fail closed on tenancy contract errors
        raise GatewayError(401, "invalid_tenant_context", "trusted tenant context is invalid") from error
    return value


def _services(request: Request) -> GatewayServices:
    return request.app.state.gateway_services


def _tenant_context(request: Request) -> Any:
    context = getattr(request.state, "tenant_context", None)
    if context is None:
        # All authenticated routes call _principal first.  Keep this helper
        # fail-closed if a future route forgets that dependency.
        raise GatewayError(500, "tenant_context_missing", "tenant context was not initialized")
    return context


def _human(principal: RequestPrincipal) -> None:
    if principal.actor_kind != "human":
        raise GatewayError(403, "human_confirmation_required", "approval requires a human actor")


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
        raise GatewayError(503, "workspace_store_unavailable", "workspace store is unavailable") from error
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
    return workspace


def _conversation(
    services: GatewayServices,
    principal: RequestPrincipal,
    conversation_id: str,
) -> dict[str, Any]:
    try:
        conversation_id = validate_identifier(conversation_id, "conversation_id", max_length=256)
        conversation = services.conversations.get_conversation(
            conversation_id, workspace_id=principal.workspace_id
        )
    except (ValueError, IdentityError) as error:
        raise GatewayError(400, "invalid_conversation_id", str(error)) from error
    if conversation is None:
        # Return the same 404 for another tenant as for an unknown ID.  This
        # prevents the endpoint from becoming a cross-tenant existence oracle.
        raise GatewayError(404, "conversation_not_found", "conversation was not found")
    return conversation


def _permission_for_principal(
    services: GatewayServices,
    principal: RequestPrincipal,
    request_id: str,
):
    try:
        item = services.permissions.get(
            validate_identifier(request_id, "request_id", max_length=256),
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
        )
    except (ValueError, IdentityError) as error:
        raise GatewayError(400, "invalid_permission_id", str(error)) from error
    except (PermissionStoreError, sqlite3.Error) as error:
        raise _map_store_error(error) from error
    if item is None or item.workspace_id != principal.workspace_id:
        raise GatewayError(404, "permission_not_found", "permission request was not found")
    return item


def _serialize_workflow_result(result: Any) -> dict[str, Any]:
    return {
        "run": _model_json(result.run),
        "steps": [_model_json(step) for step in result.steps],
        "approval": _model_json(result.approval) if result.approval is not None else None,
    }


def _workflow_run_for_principal(services: GatewayServices, principal: RequestPrincipal, run_id: str):
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
    except (WorkflowConflictError, sqlite3.Error) as error:
        raise _map_store_error(error) from error
    if run is None:
        raise GatewayError(404, "workflow_run_not_found", "workflow run was not found")
    return run


def _map_store_error(error: Exception) -> GatewayError:
    if isinstance(error, (PermissionConflictError, WorkflowConflictError)):
        return GatewayError(409, "state_conflict", str(error))
    if isinstance(error, PermissionBindingError):
        return GatewayError(409, "permission_binding_conflict", str(error))
    if isinstance(error, PermissionNotFoundError):
        return GatewayError(404, "permission_not_found", "permission request was not found")
    if isinstance(error, PermissionValidationError):
        return GatewayError(422, "invalid_permission_request", str(error))
    if isinstance(error, PermissionStoreError):
        return GatewayError(503, "permission_store_error", "permission store is unavailable")
    if isinstance(error, sqlite3.Error):
        return GatewayError(503, "storage_unavailable", "persistent storage is unavailable")
    if isinstance(error, KeyError):
        return GatewayError(404, "resource_not_found", str(error).strip("'"))
    if isinstance(error, (ValueError, ValidationError)):
        return GatewayError(422, "invalid_request", str(error))
    if isinstance(error, PermissionError):
        return GatewayError(403, "operation_not_allowed", str(error))
    return GatewayError(500, "internal_error", "the gateway could not complete the request")


def _workflow_definition_from_request(
    payload: WorkflowDefinitionRequest,
    principal: RequestPrincipal,
    services: GatewayServices,
):
    from artpm_agent.workflows.models import (
        WorkflowDefinition,
        WorkflowStepDefinition,
        WorkflowTrigger,
    )
    from artpm_agent.workflows.risk_policy import (
        DEFAULT_RISK_POLICY,
        DEFAULT_SKILL_CAPABILITIES,
    )
    from artpm_agent.workflows.designer import parse_input_map

    raw = payload.model_dump(mode="python")
    capability_allowlist = DEFAULT_SKILL_CAPABILITIES
    if services.workflow_capability_provider is not None:
        provided = services.workflow_capability_provider()
        if not isinstance(provided, Mapping):
            raise GatewayServiceError(
                "workflow capability provider returned an invalid allowlist"
            )
        capability_allowlist = provided
    trigger_raw = raw.pop("trigger")
    trigger = WorkflowTrigger.model_validate(
        {
            **trigger_raw,
            "keywords": tuple(trigger_raw.get("keywords", [])),
            "required_context_keys": tuple(trigger_raw.get("required_context_keys", [])),
            "attachment_extensions": tuple(trigger_raw.get("attachment_extensions", [])),
            "project_statuses": tuple(trigger_raw.get("project_statuses", [])),
        }
    )
    requested_steps = raw.pop("steps")
    raw.pop("version", None)
    steps: list[Any] = []
    for item in requested_steps:
        # Client-provided side_effect/approval/read_only values are hints only;
        # the server derives the effective policy from its allowlist.
        skill_id = str(item.get("skill_id") or "").strip()
        capability = str(item.get("capability") or "").strip()
        allowed = capability_allowlist.get(skill_id, frozenset())
        if capability not in allowed:
            raise GatewayError(
                403,
                "workflow_capability_not_allowed",
                f"skill/capability is outside the server allowlist: {skill_id}:{capability}",
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
    steps = tuple(steps)
    # The server owns these fields.  They are absent from the public request
    # model and cannot be used to move a definition into another tenant.
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
            "steps": steps,
            "workspace_id": principal.workspace_id,
            "profile_id": principal.profile_id,
            "tenant_id": principal.tenant_id,
            "source": "custom",
            "read_only": all(not step.side_effect for step in steps),
        }
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
        environment = (os.getenv("ARTPM_ENV") or os.getenv("ENV") or "development").strip().lower()
        require_gateway_secret = environment in {"prod", "production"}
    if identity_resolver is None:
        resolver = TrustedHeaderIdentityResolver(
            gateway_secret,
            require_secret=require_gateway_secret,
        )
        if require_gateway_secret and not resolver.gateway_secret:
            raise RuntimeError(
                "ARTPM_GATEWAY_SHARED_SECRET is required in production"
            )
    else:
        resolver = identity_resolver
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
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
        embed_gateway.settings = None
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
    async def request_context(request: Request, call_next: Callable[..., Any]):
        incoming = request.headers.get("x-request-id", "")
        try:
            request.state.request_id = validate_identifier(
                incoming, "request_id", max_length=128
            )
        except IdentityError:
            request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, error: GatewayError):
        return JSONResponse(status_code=error.status_code, content=_error_payload(request, error))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError):
        safe_errors = _safe_validation_errors(error.errors())
        gateway_error = GatewayError(422, "validation_error", "request validation failed")
        payload = _error_payload(request, gateway_error)
        payload["error"]["fields"] = safe_errors
        return JSONResponse(status_code=422, content=payload)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, error: StarletteHTTPException):
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
        return JSONResponse(status_code=error.status_code, content=_error_payload(request, gateway_error))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, error: Exception):
        logger.exception("Unhandled API error [%s]", _request_id(request), exc_info=error)
        gateway_error = GatewayError(500, "internal_error", "the gateway could not complete the request")
        return JSONResponse(status_code=500, content=_error_payload(request, gateway_error))

    def _health_response(request: Request, *, readiness: bool) -> JSONResponse:
        try:
            result = services.health()
        except Exception:
            logger.exception("API health check failed")
            result = {"status": "unhealthy", "checks": {"gateway": {"status": "error"}}}
        try:
            result.setdefault("optional", {})["voice"] = _json_safe(
                request.app.state.voice_broker.status()
            )
        except Exception:  # noqa: BLE001 - optional channel never blocks core health
            result.setdefault("optional", {})["voice"] = {
                "enabled": False,
                "realtime_ready": False,
                "status": "unavailable",
            }
        result.update({"service": "artpm-agent-api", "version": API_VERSION})
        if readiness:
            result["ready"] = result.get("status") == "ok"
            status_code = 200 if result["ready"] else 503
        else:
            status_code = 200 if result.get("status") in {"ok", "degraded"} else 503
        return JSONResponse(status_code=status_code, content=result, headers={"x-request-id": _request_id(request)})

    @app.get("/health", tags=["system"])
    def health(request: Request):
        return _health_response(request, readiness=False)

    @app.get("/ready", tags=["system"])
    def ready(request: Request):
        """Accept traffic only when every required persistence store is ready."""
        return _health_response(request, readiness=True)

    @app.get("/v1/capabilities", tags=["capabilities"])
    def capabilities(request: Request):
        principal = _principal(request)
        _require_workspace(services, principal, request)
        try:
            capabilities_value = services.capability_provider()
        except Exception as error:  # noqa: BLE001 - normalize provider errors
            raise GatewayError(503, "capabilities_unavailable", "capability registry is unavailable") from error
        if isinstance(capabilities_value, Mapping):
            entries = [
                {"name": str(name), **(_json_safe(value) if isinstance(value, Mapping) else {"description": str(value)})}
                for name, value in capabilities_value.items()
            ]
        else:
            entries = [_json_safe(item) for item in (capabilities_value or [])]
        deployment = []
        deployment_provider = services.deployment_capability_provider
        if deployment_provider is not None:
            try:
                deployment = _json_safe(deployment_provider())
            except Exception as error:  # noqa: BLE001 - diagnostics must not hide skills
                logger.warning("deployment capability report unavailable: %s", error)
        return {
            "workspace_id": principal.workspace_id,
            "items": entries,
            "deployment": deployment,
        }

    def _embed_gateway(request: Request) -> EmbedGateway:
        gateway = getattr(request.app.state, "embed_gateway", None)
        if not isinstance(gateway, EmbedGateway) or gateway.settings is None:
            raise GatewayError(503, "embed_unconfigured", "embed configuration is invalid")
        return gateway

    def _embed_rate_key(request: Request) -> str:
        client = getattr(request, "client", None)
        return str(getattr(client, "host", "unknown") or "unknown")[:128]

    def _embed_request_origin(
        request: Request,
        supplied: str,
        *,
        require_header: bool = False,
    ) -> str:
        header_origin = str(request.headers.get("origin") or "").strip()
        # Browsers always send Origin for these cross-site POSTs. Requiring an
        # exact match prevents a caller from authenticating one origin while
        # placing another origin in the JSON body.
        if require_header and not header_origin:
            raise EmbedError(403, "embed_origin_required", "request Origin header is required")
        if header_origin and header_origin.rstrip("/").lower() != str(supplied).strip().rstrip("/").lower():
            raise EmbedError(403, "embed_origin_mismatch", "request origin does not match payload origin")
        return supplied

    def _embed_principal(
        gateway: EmbedGateway,
        channel_name: str,
        claims: Mapping[str, Any],
        request: Request,
    ) -> RequestPrincipal:
        channel = gateway.settings.channels.get(channel_name)
        if channel is None:
            raise GatewayError(404, "embed_not_found", "embed channel is not available")
        try:
            principal = RequestPrincipal(
                workspace_id=channel.workspace_id,
                actor_id=validate_identifier(str(claims.get("actor_id") or "embed-user"), "actor_id"),
                actor_role="user",
                actor_kind="service",
                profile_id=channel.profile_id,
                tenant_id=channel.tenant_id,
            )
            request_context = principal.tenant_context(request_id=_request_id(request))
            request.state.tenant_context = request_context
            return principal
        except (IdentityError, ValueError) as error:
            raise GatewayError(401, "embed_token_invalid", "embed session identity is invalid") from error

    @app.get("/embed/{channel}/config", tags=["embed"])
    def embed_config(request: Request, channel: str):
        gateway = _embed_gateway(request)
        origin = request.headers.get("origin", "")
        try:
            result = gateway.public_config(channel, origin)
            configured_channel = gateway.settings.channels[channel]
        except EmbedError as error:
            raise GatewayError(error.status_code, error.code, error.message) from error
        return JSONResponse(
            status_code=200,
            content=_json_safe(result),
            headers={**gateway.response_headers(configured_channel), "x-request-id": _request_id(request)},
        )

    @app.post("/embed/{channel}/exchange", tags=["embed"])
    def embed_exchange(request: Request, channel: str, payload: EmbedExchangeRequest):
        gateway = _embed_gateway(request)
        try:
            result = gateway.exchange(
                channel,
                origin=_embed_request_origin(request, payload.origin),
                publish_token=payload.publish_token,
                rate_key=_embed_rate_key(request),
            )
            configured_channel = gateway.settings.channels[channel]
        except EmbedError as error:
            raise GatewayError(error.status_code, error.code, error.message) from error
        return JSONResponse(
            status_code=200,
            content=_json_safe(result),
            headers={**gateway.response_headers(configured_channel), "x-request-id": _request_id(request)},
        )

    @app.post("/embed/{channel}/session", tags=["embed"])
    def embed_session(request: Request, channel: str, payload: EmbedSessionRequest):
        gateway = _embed_gateway(request)
        try:
            exchange_claims = gateway.verify_exchange(
                channel,
                exchange_token=payload.exchange_token,
                origin=_embed_request_origin(request, payload.origin, require_header=True),
            )
            principal = _embed_principal(gateway, channel, exchange_claims, request)
            _require_workspace(services, principal, request)
            conversation = (
                _conversation(services, principal, payload.conversation_id)
                if payload.conversation_id
                else services.conversations.create_conversation(
                    "Embed chat",
                    workspace_id=principal.workspace_id,
                )
            )
            result = gateway.create_session(
                channel,
                exchange_token=payload.exchange_token,
                origin=payload.origin,
                conversation_id=conversation["id"],
                rate_key=_embed_rate_key(request),
            )
            configured_channel = gateway.settings.channels[channel]
        except EmbedError as error:
            raise GatewayError(error.status_code, error.code, error.message) from error
        except GatewayError:
            raise
        except Exception as error:  # noqa: BLE001 - embed must not expose store details
            logger.exception("Embed session creation failed [%s]", _request_id(request))
            raise GatewayError(503, "embed_session_failed", "embed session could not be created") from error
        result["conversation_id"] = conversation["id"]
        return JSONResponse(
            status_code=201,
            content=_json_safe(result),
            headers={**gateway.response_headers(configured_channel), "x-request-id": _request_id(request)},
        )

    @app.post("/embed/{channel}/chat", tags=["embed"])
    async def embed_chat(request: Request, channel: str, payload: EmbedChatRequest):
        gateway = _embed_gateway(request)
        try:
            claims = gateway.verify_session(
                channel,
                token=payload.session_token,
                origin=_embed_request_origin(request, payload.origin, require_header=True),
                rate_key=_embed_rate_key(request),
            )
            principal = _embed_principal(gateway, channel, claims, request)
            _require_workspace(services, principal, request)
            conversation_id = validate_identifier(
                str(claims.get("conversation_id") or ""), "conversation_id", max_length=256
            )
            conversation = _conversation(services, principal, conversation_id)
            turn_id = uuid4().hex
            user_message = services.conversations.add_message(
                conversation["id"],
                "user",
                payload.message,
                turn_id=turn_id,
                workspace_id=principal.workspace_id,
                metadata={"source": "embed", "channel": channel},
            )
            outcome = await services.chat_async(
                ChatCommand(
                    principal=principal,
                    conversation_id=conversation["id"],
                    turn_id=turn_id,
                    message=payload.message,
                    tenant_context=_tenant_context(request),
                    before_message_id=(
                        int(user_message["id"])
                        if isinstance(user_message, Mapping) and user_message.get("id")
                        else None
                    ),
                )
            )
            if isinstance(outcome, Mapping):
                outcome = ChatOutcome(**dict(outcome))
            if not isinstance(outcome, ChatOutcome):
                raise GatewayServiceError("chat handler returned an invalid outcome")
            services.conversations.add_message(
                conversation["id"],
                "assistant",
                outcome.response or "请求未返回有效内容。",
                status="complete" if outcome.success else "error",
                turn_id=turn_id,
                workspace_id=principal.workspace_id,
                metadata={
                    "source": "embed",
                    "handled_by": outcome.handled_by,
                    "awaiting_approval": outcome.awaiting_approval,
                    "error": "chat_failed" if outcome.error else None,
                },
            )
            configured_channel = gateway.settings.channels[channel]
        except EmbedError as error:
            raise GatewayError(error.status_code, error.code, error.message) from error
        except GatewayError:
            raise
        except Exception as error:  # noqa: BLE001 - never expose provider internals
            logger.exception("Embed chat failed [%s]", _request_id(request))
            raise GatewayError(502, "embed_chat_failed", "embed chat could not complete the request") from error
        metadata = _json_safe(outcome.metadata)
        return JSONResponse(
            status_code=202 if outcome.awaiting_approval else (200 if outcome.success else 502),
            content={
                "conversation_id": conversation["id"],
                "turn_id": turn_id,
                "response": outcome.response or "请求未返回有效内容。",
                "success": outcome.success,
                "awaiting_approval": outcome.awaiting_approval,
                "handled_by": outcome.handled_by,
                "metadata": metadata,
                "artifacts": _json_safe(outcome.artifacts),
            },
            headers={**gateway.response_headers(configured_channel), "x-request-id": _request_id(request)},
        )

    @app.get("/v1/workspaces", tags=["workspaces"])
    def list_workspaces(request: Request):
        """List workspaces visible to the authenticated tenant."""
        principal = _principal(request)
        try:
            items = services.conversations.list_workspaces(
                # A tenant may own workspaces backed by different agent
                # profiles. Profile selection is an execution concern and
                # must not hide otherwise authorized workspace metadata.
                profile_id=None,
                limit=100,
            )
            visible = [
                item
                for item in items
                # Unbound legacy rows are intentionally hidden until a
                # workspace-scoped request claims them through the normal
                # tenant guard. Never expose them from a list endpoint.
                if item.get("tenant_id") == principal.tenant_id
            ]
        except Exception as error:  # noqa: BLE001 - normalize store errors
            raise GatewayError(503, "workspace_store_unavailable", "workspace store is unavailable") from error
        return {"tenant_id": principal.tenant_id, "items": [_json_safe(item) for item in visible]}

    @app.post("/v1/workspaces", tags=["workspaces"])
    def create_workspace(request: Request, payload: WorkspaceCreateRequest):
        """Create one workspace owned by the trusted tenant context."""
        principal = _principal(request)
        if principal.actor_role != "admin":
            raise GatewayError(403, "workspace_admin_required", "workspace creation requires admin role")
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
                raise GatewayError(409, "workspace_exists", "workspace already exists") from error
            raise GatewayError(503, "workspace_store_unavailable", "workspace could not be created") from error
        return JSONResponse(status_code=201, content=_json_safe(item), headers={"x-request-id": _request_id(request)})

    @app.post("/v1/search", tags=["knowledge"])
    def search_knowledge(request: Request, payload: KnowledgeSearchRequest):
        """Search only the current workspace knowledge scope."""
        principal = _principal(request)
        _require_workspace(services, principal, request)
        handler = services.knowledge_search_handler
        if handler is None:
            raise GatewayError(503, "knowledge_search_unavailable", "workspace knowledge retrieval is unavailable")
        try:
            items = handler(principal, payload.model_dump(mode="json"))
        except GatewayServiceError as error:
            raise GatewayError(503, "knowledge_search_unavailable", str(error)) from error
        except (ValueError, TypeError) as error:
            raise GatewayError(400, "invalid_search_request", str(error)) from error
        except Exception as error:  # noqa: BLE001 - normalize provider errors
            logger.exception("Knowledge search failed [%s]", _request_id(request))
            raise GatewayError(503, "knowledge_search_failed", "knowledge search could not complete") from error
        return {
            "workspace_id": principal.workspace_id,
            "query": payload.query,
            "items": _json_safe(items),
        }

    @app.get("/v1/voice/status", tags=["voice"])
    def voice_status(request: Request):
        principal = _principal(request)
        _require_workspace(services, principal, request)
        broker = request.app.state.voice_broker
        try:
            status = broker.status()
        except Exception as error:  # noqa: BLE001 - optional service boundary
            logger.exception("Voice status provider failed [%s]", _request_id(request))
            raise GatewayError(
                503,
                "voice_status_unavailable",
                "voice status is temporarily unavailable",
            ) from error
        return {
            "workspace_id": principal.workspace_id,
            **_json_safe(status),
        }

    @app.post("/v1/voice/sessions", tags=["voice"])
    def create_voice_session(request: Request, payload: VoiceSessionRequest):
        principal = _principal(request)
        _human(principal)
        _require_workspace(services, principal, request)
        conversation = _conversation(
            services,
            principal,
            payload.conversation_id,
        )
        broker = request.app.state.voice_broker
        try:
            details = broker.create(
                principal=principal,
                conversation_id=conversation["id"],
            )
        except VoiceUnavailableError as error:
            raise GatewayError(503, "voice_unavailable", str(error)) from error
        except VoiceConfigurationError as error:
            raise GatewayError(
                500,
                "voice_configuration_error",
                "voice service configuration is invalid",
            ) from error
        except Exception as error:  # noqa: BLE001 - never expose token internals
            logger.exception("Voice session creation failed [%s]", _request_id(request))
            raise GatewayError(
                503,
                "voice_session_failed",
                "voice session could not be created",
            ) from error
        if hasattr(details, "to_dict"):
            details = details.to_dict()
        if not isinstance(details, Mapping):
            raise GatewayError(
                500,
                "voice_contract_error",
                "voice session provider returned an invalid response",
            )
        return JSONResponse(
            status_code=201,
            content=_json_safe(details),
            headers={"x-request-id": _request_id(request)},
        )

    @app.post("/v1/chat", tags=["chat"])
    async def chat(request: Request, payload: ChatRequest):
        principal = _principal(request)
        _require_workspace(services, principal, request)
        if payload.conversation_id:
            conversation = _conversation(services, principal, payload.conversation_id)
        else:
            title = payload.title or payload.message[:80]
            try:
                conversation = services.conversations.create_conversation(
                    title,
                    workspace_id=principal.workspace_id,
                )
            except Exception as error:  # noqa: BLE001 - normalize persistence errors
                raise _map_store_error(error) from error
        turn_id = uuid4().hex
        attachments = tuple(item.model_dump(mode="json") for item in payload.attachments)
        user_message_persisted = False
        try:
            user_message = services.conversations.add_message(
                conversation["id"],
                "user",
                payload.message,
                turn_id=turn_id,
                workspace_id=principal.workspace_id,
                metadata={"attachments": list(attachments), "source": "api"},
            )
            user_message_persisted = True
            command = ChatCommand(
                principal=principal,
                conversation_id=conversation["id"],
                turn_id=turn_id,
                message=payload.message,
                attachments=attachments,
                tenant_context=_tenant_context(request),
                before_message_id=(
                    int(user_message["id"])
                    if isinstance(user_message, Mapping) and user_message.get("id")
                    else None
                ),
            )
            outcome = await services.chat_async(command)
            if isinstance(outcome, Mapping):
                outcome = ChatOutcome(**dict(outcome))
            if not isinstance(outcome, ChatOutcome):
                raise GatewayServiceError("chat handler returned an invalid outcome")
            response_text = outcome.response or "模型服务未能完成请求，请稍后重试。"
            status = "complete" if outcome.success else "error"
            services.conversations.add_message(
                conversation["id"],
                "assistant",
                outcome.response or "请求未返回有效内容",
                status=status,
                turn_id=turn_id,
                workspace_id=principal.workspace_id,
                metadata={
                    "handled_by": outcome.handled_by,
                    "awaiting_approval": outcome.awaiting_approval,
                    "metadata": _json_safe(outcome.metadata),
                    "artifacts": _json_safe(outcome.artifacts),
                    # Provider details stay in server logs/telemetry. Never
                    # persist raw exception text in a user-visible transcript.
                    "error": "chat_failed" if outcome.error else None,
                },
            )
        except GatewayError:
            raise
        except Exception as error:  # noqa: BLE001 - never leak provider internals
            logger.exception("Chat handler failed [%s]", _request_id(request))
            if user_message_persisted:
                try:
                    services.conversations.add_message(
                        conversation["id"],
                        "assistant",
                        "请求处理失败，请稍后重试。",
                        status="error",
                        turn_id=turn_id,
                        workspace_id=principal.workspace_id,
                        metadata={
                            "handled_by": "gateway",
                            "error": "chat_handler_failed",
                        },
                    )
                except Exception:  # noqa: BLE001 - preserve the original failure
                    logger.exception(
                        "Failed to persist chat error callback [%s]",
                        _request_id(request),
                    )
            raise GatewayError(502, "chat_handler_failed", "chat service could not complete the request") from error
        metadata = _json_safe(outcome.metadata)
        permission_request_id = (
            metadata.get("permission_request_id")
            if isinstance(metadata, Mapping)
            else None
        )
        response_body = {
            "conversation_id": conversation["id"],
            "turn_id": turn_id,
            "response": response_text,
            "success": outcome.success,
            "awaiting_approval": outcome.awaiting_approval,
            "permission_request_id": permission_request_id,
            "handled_by": outcome.handled_by,
            "metadata": metadata,
            "artifacts": _json_safe(outcome.artifacts),
        }
        status_code = 202 if outcome.awaiting_approval else (200 if outcome.success else 502)
        if not outcome.success and not outcome.awaiting_approval:
            response_body["error"] = {
                "code": "chat_failed",
                "message": "模型服务未能完成请求，请稍后重试。",
                "request_id": _request_id(request),
            }
        return JSONResponse(
            status_code=status_code,
            content=response_body,
            headers={"x-request-id": _request_id(request)},
        )

    @app.post("/v1/chat/stream", tags=["chat"])
    async def chat_stream(
        request: Request,
        payload: ChatStreamRequest,
        after_sequence: int | None = Query(default=None, ge=0),
    ):
        """Stream one tenant-scoped turn as resumable Server-Sent Events.

        The route accepts a provider-neutral ``chat_stream_handler`` when a
        host supplies one.  The local adapter bridges the existing async chat
        handler and its EventBus, so migrating providers does not require a
        second orchestration path.  Every frame carries ``run_id`` and
        ``turn_id`` in both the SSE id and JSON payload; clients can persist
        those values before reconnecting.  ``after_sequence`` is reserved for
        session-log replay adapters and is accepted for forward compatibility.
        """

        principal = _principal(request)
        _require_workspace(services, principal, request)
        if payload.conversation_id:
            conversation = _conversation(services, principal, payload.conversation_id)
        else:
            title = payload.title or payload.message[:80]
            try:
                conversation = services.conversations.create_conversation(
                    title,
                    workspace_id=principal.workspace_id,
                )
            except Exception as error:  # noqa: BLE001 - normalize persistence errors
                raise _map_store_error(error) from error

        try:
            turn_id = validate_identifier(
                payload.turn_id or uuid4().hex,
                "turn_id",
                max_length=256,
            )
            run_id = validate_identifier(
                payload.run_id or turn_id,
                "run_id",
                max_length=256,
            )
        except IdentityError as error:
            raise GatewayError(400, "invalid_stream_id", str(error)) from error

        attachments = tuple(item.model_dump(mode="json") for item in payload.attachments)
        try:
            user_message = services.conversations.add_message(
                conversation["id"],
                "user",
                payload.message,
                turn_id=turn_id,
                workspace_id=principal.workspace_id,
                metadata={"attachments": list(attachments), "source": "api-stream"},
            )
        except Exception as error:  # noqa: BLE001 - normalize persistence errors
            raise _map_store_error(error) from error

        command = ChatCommand(
            principal=principal,
            conversation_id=conversation["id"],
            turn_id=turn_id,
            message=payload.message,
            attachments=attachments,
            tenant_context=_tenant_context(request),
            before_message_id=(
                int(user_message["id"])
                if isinstance(user_message, Mapping) and user_message.get("id")
                else None
            ),
            run_id=run_id,
        )

        async def stream_frames():
            event_index = 0
            started = False
            ended = False
            response_parts: list[str] = []
            outcome: ChatOutcome | None = None
            unsubscribe: Callable[[], Any] | None = None
            task: asyncio.Task[Any] | None = None

            async def emit(value: Any, *, final: bool = False):
                nonlocal event_index, started, ended
                event_payload = _stream_event_payload(
                    value,
                    run_id=run_id,
                    turn_id=turn_id,
                )
                if event_payload is None:
                    return
                event_type = str(event_payload.get("type") or "message_update")
                if event_type == "turn_start":
                    if started:
                        return
                    started = True
                elif event_type == "turn_end":
                    if not final:
                        return
                    if ended:
                        return
                    ended = True
                if event_type in {"message_update", "message_delta"}:
                    delta = event_payload.get("delta")
                    if isinstance(delta, str) and delta:
                        response_parts.append(delta)
                    elif not response_parts:
                        content = event_payload.get("content")
                        if isinstance(content, str) and content:
                            response_parts.append(content)
                event_id = f"{run_id}:{turn_id}:{event_index}"
                event_index += 1
                yield _sse_frame(event_payload, event_id=event_id)

            # A synthetic first frame gives every adapter one stable lifecycle
            # marker.  A duplicate TURN_START from the adapter is suppressed.
            async for frame in emit(
                {
                    "type": "turn_start",
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "metadata": {
                        "workspace_id": principal.workspace_id,
                        "conversation_id": conversation["id"],
                        "after_sequence": after_sequence,
                    },
                }
            ):
                yield frame

            try:
                stream_handler = services.chat_stream_handler
                if stream_handler is not None:
                    callable_target = getattr(stream_handler, "__call__", stream_handler)
                    if inspect.iscoroutinefunction(stream_handler) or inspect.iscoroutinefunction(callable_target):
                        source = stream_handler(command)
                    else:
                        source = await asyncio.to_thread(stream_handler, command)
                    if inspect.isawaitable(source):
                        source = await source
                    direct_outcome = _coerce_chat_outcome(source)
                    if direct_outcome is not None:
                        outcome = direct_outcome
                    elif isinstance(source, Mapping):
                        async for frame in emit(source):
                            yield frame
                    elif isinstance(source, str):
                        async for frame in emit(source):
                            yield frame
                    elif isinstance(source, AsyncIterable) or hasattr(source, "__aiter__"):
                        async for item in source:
                            direct_outcome = _coerce_chat_outcome(item)
                            if direct_outcome is not None:
                                outcome = direct_outcome
                                continue
                            async for frame in emit(item):
                                yield frame
                    elif isinstance(source, Iterable) and not isinstance(source, (str, bytes)):
                        iterator = iter(source)

                        def next_item():
                            try:
                                return True, next(iterator)
                            except StopIteration:
                                return False, None

                        while True:
                            has_item, item = await asyncio.to_thread(next_item)
                            if not has_item:
                                break
                            direct_outcome = _coerce_chat_outcome(item)
                            if direct_outcome is not None:
                                outcome = direct_outcome
                                continue
                            async for frame in emit(item):
                                yield frame
                    elif source is not None:
                        direct_outcome = _coerce_chat_outcome(source)
                        if direct_outcome is not None:
                            outcome = direct_outcome
                else:
                    # The default local runtime emits SESSION lifecycle events
                    # synchronously on its EventBus while chat_async runs in a
                    # worker thread. Forward those observations without
                    # blocking the gateway event loop.
                    event_queue: Queue[Any] = Queue()
                    event_bus = services.event_bus

                    def observe(event: Any) -> None:
                        event_run = str(getattr(event, "run_id", "") or "")
                        event_turn = str(getattr(event, "turn_id", "") or "")
                        if event_run != run_id and event_turn != turn_id:
                            return
                        # EventBus callbacks run in the provider thread. A
                        # standard queue makes the handoff lossless even when
                        # the provider returns immediately after publishing.
                        event_queue.put(event)

                    if event_bus is not None and callable(getattr(event_bus, "subscribe", None)):
                        unsubscribe = event_bus.subscribe(observe)
                    task = asyncio.create_task(services.chat_async(command))
                    while True:
                        if task.done() and event_queue.empty():
                            break
                        try:
                            event = await asyncio.to_thread(event_queue.get, True, 0.1)
                        except QueueEmpty:
                            continue
                        async for frame in emit(event):
                            yield frame
                    outcome_value = await task
                    outcome = _coerce_chat_outcome(outcome_value)
                    if outcome is None:
                        raise GatewayServiceError("chat handler returned an invalid outcome")
            except asyncio.CancelledError:
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        # Client disconnects must not leak the provider worker;
                        # the original cancellation remains the public result.
                        pass
                raise
            except Exception:  # noqa: BLE001 - never leak provider internals
                logger.exception("Chat stream failed [%s]", _request_id(request))
                outcome = ChatOutcome(
                    response="",
                    success=False,
                    error="chat_failed",
                    handled_by="gateway",
                )
                async for frame in emit(
                    {
                        "type": "error",
                        "run_id": run_id,
                        "turn_id": turn_id,
                        "is_error": True,
                        "error": "chat_failed",
                        "metadata": {"request_id": _request_id(request)},
                    }
                ):
                    yield frame
            finally:
                if unsubscribe is not None:
                    try:
                        unsubscribe()
                    except Exception:  # pragma: no cover - optional bus cleanup
                        logger.debug("chat stream event unsubscribe failed", exc_info=True)
                if task is not None and not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

            if outcome is None:
                outcome = ChatOutcome(response="".join(response_parts))
            response_text = outcome.response or "".join(response_parts)
            if response_text and not response_parts:
                # Legacy/default handlers return one final string rather than
                # token events. Expose it as one delta so every stream has a
                # consistent message channel while providers migrate.
                async for frame in emit(
                    {
                        "type": "message_update",
                        "run_id": run_id,
                        "turn_id": turn_id,
                        "delta": response_text,
                        "metadata": {"source": "final_response"},
                    }
                ):
                    yield frame
            status = "complete" if outcome.success else "error"
            try:
                services.conversations.add_message(
                    conversation["id"],
                    "assistant",
                    response_text or ("请求处理失败，请稍后重试。" if not outcome.success else ""),
                    status=status,
                    turn_id=turn_id,
                    workspace_id=principal.workspace_id,
                    metadata={
                        "run_id": run_id,
                        "handled_by": outcome.handled_by,
                        "awaiting_approval": outcome.awaiting_approval,
                        "metadata": _json_safe(outcome.metadata),
                        "artifacts": _json_safe(outcome.artifacts),
                        "error": "chat_failed" if outcome.error else None,
                    },
                )
            except Exception:  # noqa: BLE001 - stream result remains inspectable
                logger.exception("Failed to persist streamed assistant message [%s]", _request_id(request))

            async for frame in emit(
                {
                    "type": "snapshot",
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "response": response_text,
                    "success": bool(outcome.success),
                    "awaiting_approval": bool(outcome.awaiting_approval),
                    "handled_by": outcome.handled_by,
                    "metadata": _json_safe(outcome.metadata),
                    "artifacts": _json_safe(outcome.artifacts),
                    "error": "chat_failed" if outcome.error else None,
                },
            ):
                yield frame
            async for frame in emit(
                {
                    "type": "turn_end",
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "is_error": not outcome.success,
                    "error": "chat_failed" if not outcome.success else None,
                    "metadata": {
                        "success": bool(outcome.success),
                        "handled_by": outcome.handled_by,
                        "awaiting_approval": bool(outcome.awaiting_approval),
                    },
                },
                final=True,
            ):
                yield frame

        return StreamingResponse(
            stream_frames(),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache, no-transform",
                "connection": "keep-alive",
                "x-accel-buffering": "no",
                "x-request-id": _request_id(request),
            },
        )

    @app.get("/v1/permissions", tags=["permissions"])
    def list_permissions(
        request: Request,
        conversation_id: str | None = Query(default=None, max_length=256),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        principal = _principal(request)
        _require_workspace(services, principal, request)
        if conversation_id is not None:
            conversation_id = validate_identifier(conversation_id, "conversation_id", max_length=256)
        try:
            items = services.permissions.list_pending(
                workspace_id=principal.workspace_id,
                tenant_id=principal.tenant_id,
                conversation_id=conversation_id,
                limit=limit,
            )
        except Exception as error:
            raise _map_store_error(error) from error
        return {"items": [item.to_dict() for item in items]}

    @app.get("/v1/permissions/{request_id}", tags=["permissions"])
    def get_permission(request: Request, request_id: str):
        principal = _principal(request)
        item = _permission_for_principal(services, principal, request_id)
        return {"item": item.to_dict()}

    def _decide_permission(request: Request, request_id: str, payload: PermissionDecisionRequest, decision: str):
        principal = _principal(request)
        _human(principal)
        item = _permission_for_principal(services, principal, request_id)
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
                actor_role=principal.actor_role,
                expected_version=payload.expected_version,
                workspace_id=principal.workspace_id,
                tenant_id=principal.tenant_id,
                acknowledged_risk=(
                    payload.acknowledged_risk if decision == "approved" else None
                ),
            )
            if decision == "approved" and services.permission_executor is not None:
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
                    executor = services.permission_executor
                    try:
                        parameters = inspect.signature(executor).parameters
                    except (TypeError, ValueError):
                        parameters = {}
                    if len(parameters) >= 2:
                        result = executor(claimed, _tenant_context(request))
                    else:
                        result = executor(claimed)
                    if inspect.isawaitable(result):
                        raise GatewayServiceError("async permission executors require an async gateway adapter")
                    completed = services.permissions.complete_execution(
                        claimed.id,
                        execution_id=execution_id,
                        success=True,
                        expected_version=claimed.state_version,
                        result=result,
                        workspace_id=principal.workspace_id,
                        tenant_id=principal.tenant_id,
                    )
                except Exception as error:  # noqa: BLE001 - persist failed execution
                    services.permissions.complete_execution(
                        claimed.id,
                        execution_id=execution_id,
                        success=False,
                        expected_version=claimed.state_version,
                        error=str(error),
                        workspace_id=principal.workspace_id,
                        tenant_id=principal.tenant_id,
                    )
                    raise GatewayError(502, "permission_execution_failed", "approved operation failed") from error
                return {"item": completed.to_dict()}
        except GatewayError:
            raise
        except Exception as error:  # noqa: BLE001 - map CAS/role errors
            raise _map_store_error(error) from error
        return {"item": decided.to_dict()}

    @app.post("/v1/permissions/{request_id}/approve", tags=["permissions"])
    def approve_permission(request: Request, request_id: str, payload: PermissionDecisionRequest):
        return _decide_permission(request, request_id, payload, "approved")

    @app.post("/v1/permissions/{request_id}/reject", tags=["permissions"])
    def reject_permission(request: Request, request_id: str, payload: PermissionDecisionRequest):
        return _decide_permission(request, request_id, payload, "rejected")

    @app.get("/v1/workflows", tags=["workflows"])
    def list_workflows(request: Request, enabled_only: bool = Query(default=False)):
        principal = _principal(request)
        _require_workspace(services, principal, request)
        try:
            definitions = services.workflows.list_definitions(
                workspace_id=principal.workspace_id,
                profile_id=principal.profile_id,
                tenant_id=principal.tenant_id,
                enabled_only=enabled_only,
            )
        except Exception as error:
            raise _map_store_error(error) from error
        return {"items": [_model_json(item) for item in definitions]}

    @app.post("/v1/workflows", tags=["workflows"])
    def create_workflow(request: Request, payload: WorkflowDefinitionRequest):
        principal = _principal(request)
        _human(principal)
        if principal.actor_role != "admin":
            raise GatewayError(403, "admin_required", "creating a workflow requires an admin actor")
        _require_workspace(services, principal, request)
        try:
            definition = _workflow_definition_from_request(payload, principal, services)
            stored = services.workflows.put_definition(definition)
        except GatewayError:
            raise
        except Exception as error:
            raise _map_store_error(error) from error
        return {"item": _model_json(stored)}

    @app.get("/v1/workflows/{workflow_id}", tags=["workflows"])
    def get_workflow(request: Request, workflow_id: str, version: int | None = Query(default=None, ge=1)):
        principal = _principal(request)
        _require_workspace(services, principal, request)
        try:
            workflow_id = validate_identifier(workflow_id, "workflow_id", max_length=128)
            definition = services.workflows.get_definition(
                workflow_id,
                version=version,
                workspace_id=principal.workspace_id,
                profile_id=principal.profile_id,
                tenant_id=principal.tenant_id,
            )
        except Exception as error:
            raise _map_store_error(error) from error
        if definition is None:
            raise GatewayError(404, "workflow_not_found", "workflow was not found")
        return {"item": _model_json(definition)}

    @app.post("/v1/workflows/{workflow_id}/runs", tags=["workflows"])
    def run_workflow(request: Request, workflow_id: str, payload: WorkflowRunRequest):
        principal = _principal(request)
        _require_workspace(services, principal, request)
        _conversation(services, principal, payload.conversation_id)
        try:
            workflow_id = validate_identifier(workflow_id, "workflow_id", max_length=128)
            definition = services.workflows.get_definition(
                workflow_id,
                version=payload.version,
                workspace_id=principal.workspace_id,
                profile_id=principal.profile_id,
                tenant_id=principal.tenant_id,
            )
            if definition is None:
                raise GatewayError(404, "workflow_not_found", "workflow was not found")
            engine = services.get_workflow_engine(_tenant_context(request))
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
            raise _map_store_error(error) from error
        return _serialize_workflow_result(result)

    @app.get("/v1/workflow-runs", tags=["workflows"])
    def list_workflow_runs(
        request: Request,
        conversation_id: str = Query(..., min_length=1, max_length=256),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        principal = _principal(request)
        _conversation(services, principal, conversation_id)
        try:
            runs = services.workflows.list_runs(
                conversation_id,
                workspace_id=principal.workspace_id,
                profile_id=principal.profile_id,
                tenant_id=principal.tenant_id,
                limit=limit,
            )
        except Exception as error:
            raise _map_store_error(error) from error
        return {"items": [_model_json(run) for run in runs]}

    @app.get("/v1/workflow-runs/{run_id}", tags=["workflows"])
    def get_workflow_run(request: Request, run_id: str):
        principal = _principal(request)
        _workflow_run_for_principal(services, principal, run_id)
        try:
            result = services.get_workflow_engine(_tenant_context(request)).result(
                run_id,
                workspace_id=principal.workspace_id,
            )
        except Exception as error:
            raise _map_store_error(error) from error
        return _serialize_workflow_result(result)

    def _decide_workflow(
        request: Request,
        run_id: str,
        step_index: int,
        payload: WorkflowApprovalRequest,
        decision: str,
    ):
        principal = _principal(request)
        _human(principal)
        _workflow_run_for_principal(services, principal, run_id)
        if step_index < 0 or step_index > 7:
            raise GatewayError(400, "invalid_step_index", "step_index is out of range")
        try:
            result = services.get_workflow_engine(_tenant_context(request)).decide_approval(
                run_id,
                step_index,
                decision=decision,
                actor=principal.actor_id,
                actor_level=principal.actor_role,
                note=payload.note,
                auto_resume=True,
                workspace_id=principal.workspace_id,
            )
        except Exception as error:
            raise _map_store_error(error) from error
        return _serialize_workflow_result(result)

    @app.post("/v1/workflow-runs/{run_id}/steps/{step_index}/approve", tags=["workflows"])
    def approve_workflow(request: Request, run_id: str, step_index: int, payload: WorkflowApprovalRequest):
        return _decide_workflow(request, run_id, step_index, payload, "approved")

    @app.post("/v1/workflow-runs/{run_id}/steps/{step_index}/reject", tags=["workflows"])
    def reject_workflow(request: Request, run_id: str, step_index: int, payload: WorkflowApprovalRequest):
        return _decide_workflow(request, run_id, step_index, payload, "rejected")

    return app


__all__ = ["API_VERSION", "GatewayError", "create_app"]
