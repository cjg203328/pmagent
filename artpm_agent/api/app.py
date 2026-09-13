"""FastAPI application factory for the ArtPM agent gateway.

The factory is intentionally dependency-injected.  A cloud deployment can
provide a remote harness, plugin registry, and tenant-aware stores while the
local default adapter remains available for ``uvicorn`` and development.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
import inspect
import json
import logging
import os
import sqlite3
from typing import Any, Callable
from uuid import uuid4

try:  # Keep import errors actionable when the optional API extra is omitted.
    from fastapi import FastAPI, Query, Request
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
from artpm_agent.workflows.store import WorkflowConflictError
from artpm_agent.voice import (
    VoiceConfigurationError,
    VoiceSessionBroker,
    VoiceUnavailableError,
)

from .models import (
    ChatRequest,
    PermissionDecisionRequest,
    WorkflowApprovalRequest,
    WorkflowDefinitionRequest,
    WorkflowRunRequest,
    VoiceSessionRequest,
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
            run_id, workspace_id=principal.workspace_id
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    app.state.gateway_services = services
    app.state.identity_resolver = resolver
    app.state.voice_broker = voice_broker or VoiceSessionBroker()

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
        return {"workspace_id": principal.workspace_id, "items": entries}

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
                    )
                except Exception as error:  # noqa: BLE001 - persist failed execution
                    services.permissions.complete_execution(
                        claimed.id,
                        execution_id=execution_id,
                        success=False,
                        expected_version=claimed.state_version,
                        error=str(error),
                        workspace_id=principal.workspace_id,
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
                run_id, workspace_id=principal.workspace_id
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
