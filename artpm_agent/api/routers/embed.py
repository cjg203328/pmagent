"""Origin-bound embed gateway routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..embed import EmbedError, EmbedGateway, _origin
from ..errors import GatewayError
from ..models import EmbedChatRequest, EmbedExchangeRequest, EmbedSessionRequest
from ..services import (
    ChatCommand,
    ChatOutcome,
    GatewayServiceError,
    GatewayServices,
    IdentityError,
    RequestPrincipal,
    validate_identifier,
)


def create_embed_router(
    *,
    services: GatewayServices,
    principal_for_request: Callable[[Request], RequestPrincipal],
    require_workspace: Callable[[GatewayServices, RequestPrincipal, Request], None],
    require_conversation: Callable[
        [GatewayServices, RequestPrincipal, str], dict[str, Any]
    ],
    tenant_context: Callable[[Request], Any],
    request_id: Callable[[Request], str],
    normalize_json: Callable[[Any], Any],
    embed_gateway: EmbedGateway,
) -> APIRouter:
    """Create embed routes with all application dependencies injected."""
    router = APIRouter(prefix="/embed/{channel}", tags=["embed"])

    def gateway_or_error() -> EmbedGateway:
        if (
            not isinstance(embed_gateway, EmbedGateway)
            or embed_gateway.settings is None
        ):
            raise GatewayError(
                503, "embed_unconfigured", "embed configuration is invalid"
            )
        return embed_gateway

    def rate_key(request: Request) -> str:
        client = getattr(request, "client", None)
        return str(getattr(client, "host", "unknown") or "unknown")[:128]

    def request_origin(
        request: Request, supplied: str, *, require_header: bool = False
    ) -> str:
        supplied_origin = _origin(supplied)
        header_value = str(request.headers.get("origin") or "").strip()
        header_origin = _origin(header_value) if header_value else ""
        if require_header and not header_origin:
            raise EmbedError(
                403, "embed_origin_required", "request Origin header is required"
            )
        if header_origin and header_origin != supplied_origin:
            raise EmbedError(
                403,
                "embed_origin_mismatch",
                "request origin does not match payload origin",
            )
        return supplied_origin

    def principal(
        gateway: EmbedGateway,
        channel_name: str,
        claims: Mapping[str, Any],
        request: Request,
    ) -> RequestPrincipal:
        channel = gateway.settings.channels.get(channel_name)
        if channel is None:
            raise GatewayError(404, "embed_not_found", "embed channel is not available")
        try:
            resolved = RequestPrincipal(
                workspace_id=channel.workspace_id,
                actor_id=validate_identifier(
                    str(claims.get("actor_id") or "embed-user"), "actor_id"
                ),
                actor_role="user",
                actor_kind="service",
                profile_id=channel.profile_id,
                tenant_id=channel.tenant_id,
            )
            request.state.tenant_context = resolved.tenant_context(
                request_id=request_id(request)
            )
            return resolved
        except (IdentityError, ValueError) as error:
            raise GatewayError(
                401, "embed_token_invalid", "embed session identity is invalid"
            ) from error

    @router.get("/config")
    def config(request: Request, channel: str) -> JSONResponse:
        gateway = gateway_or_error()
        try:
            result = gateway.public_config(channel, request.headers.get("origin", ""))
            configured_channel = gateway.settings.channels[channel]
        except EmbedError as error:
            raise GatewayError(error.status_code, error.code, error.message) from error
        return JSONResponse(
            content=normalize_json(result),
            headers={
                **gateway.response_headers(configured_channel),
                "x-request-id": request_id(request),
            },
        )

    @router.post("/exchange")
    def exchange(
        request: Request, channel: str, payload: EmbedExchangeRequest
    ) -> JSONResponse:
        gateway = gateway_or_error()
        try:
            result = gateway.exchange(
                channel,
                origin=request_origin(request, payload.origin),
                publish_token=payload.publish_token,
                rate_key=rate_key(request),
            )
            configured_channel = gateway.settings.channels[channel]
        except EmbedError as error:
            raise GatewayError(error.status_code, error.code, error.message) from error
        return JSONResponse(
            content=normalize_json(result),
            headers={
                **gateway.response_headers(configured_channel),
                "x-request-id": request_id(request),
            },
        )

    @router.post("/session")
    def session(
        request: Request, channel: str, payload: EmbedSessionRequest
    ) -> JSONResponse:
        gateway = gateway_or_error()
        try:
            claims = gateway.verify_exchange(
                channel,
                exchange_token=payload.exchange_token,
                origin=request_origin(request, payload.origin, require_header=True),
            )
            resolved = principal(gateway, channel, claims, request)
            require_workspace(services, resolved, request)
            conversation = (
                require_conversation(services, resolved, payload.conversation_id)
                if payload.conversation_id
                else services.conversations.create_conversation(
                    "Embed chat", workspace_id=resolved.workspace_id
                )
            )
            result = gateway.create_session(
                channel,
                exchange_token=payload.exchange_token,
                origin=payload.origin,
                conversation_id=conversation["id"],
                rate_key=rate_key(request),
            )
            configured_channel = gateway.settings.channels[channel]
        except EmbedError as error:
            raise GatewayError(error.status_code, error.code, error.message) from error
        except GatewayError:
            raise
        except Exception as error:  # noqa: BLE001 - do not expose store details
            raise GatewayError(
                503, "embed_session_failed", "embed session could not be created"
            ) from error
        result["conversation_id"] = conversation["id"]
        return JSONResponse(
            status_code=201,
            content=normalize_json(result),
            headers={
                **gateway.response_headers(configured_channel),
                "x-request-id": request_id(request),
            },
        )

    @router.post("/chat")
    async def chat(
        request: Request, channel: str, payload: EmbedChatRequest
    ) -> JSONResponse:
        gateway = gateway_or_error()
        try:
            claims = gateway.verify_session(
                channel,
                token=payload.session_token,
                origin=request_origin(request, payload.origin, require_header=True),
                rate_key=rate_key(request),
            )
            resolved = principal(gateway, channel, claims, request)
            require_workspace(services, resolved, request)
            conversation_id = validate_identifier(
                str(claims.get("conversation_id") or ""),
                "conversation_id",
                max_length=256,
            )
            conversation = require_conversation(services, resolved, conversation_id)
            turn_id = uuid4().hex
            user_message = services.conversations.add_message(
                conversation["id"],
                "user",
                payload.message,
                turn_id=turn_id,
                workspace_id=resolved.workspace_id,
                metadata={"source": "embed", "channel": channel},
            )
            outcome = await services.chat_async(
                ChatCommand(
                    principal=resolved,
                    conversation_id=conversation["id"],
                    turn_id=turn_id,
                    message=payload.message,
                    tenant_context=tenant_context(request),
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
                workspace_id=resolved.workspace_id,
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
            raise GatewayError(
                502, "embed_chat_failed", "embed chat could not complete the request"
            ) from error
        return JSONResponse(
            status_code=202
            if outcome.awaiting_approval
            else (200 if outcome.success else 502),
            content=normalize_json(
                {
                    "conversation_id": conversation["id"],
                    "turn_id": turn_id,
                    "response": outcome.response or "请求未返回有效内容。",
                    "success": outcome.success,
                    "awaiting_approval": outcome.awaiting_approval,
                    "handled_by": outcome.handled_by,
                    "metadata": outcome.metadata,
                    "artifacts": outcome.artifacts,
                }
            ),
            headers={
                **gateway.response_headers(configured_channel),
                "x-request-id": request_id(request),
            },
        )

    return router
