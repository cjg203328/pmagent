"""Voice session routes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from artpm_agent.voice import (
    VoiceConfigurationError,
    VoiceSessionBroker,
    VoiceUnavailableError,
)

from ..errors import GatewayError
from ..models import VoiceSessionRequest
from ..services import GatewayServices, RequestPrincipal

if TYPE_CHECKING:
    from collections.abc import Callable


def create_voice_router(
    *,
    services: GatewayServices,
    principal_for_request: Callable[[Request], RequestPrincipal],
    require_workspace: Callable[[GatewayServices, RequestPrincipal, Request], None],
    require_human: Callable[[RequestPrincipal], None],
    require_conversation: Callable[
        [GatewayServices, RequestPrincipal, str | None], Mapping[str, Any]
    ],
    request_id: Callable[[Request], str],
    normalize_json: Callable[[Any], Any],
    voice_broker: VoiceSessionBroker,
) -> APIRouter:
    """Create voice session router with injected dependencies."""
    router = APIRouter(prefix="/v1/voice", tags=["voice"])
    logger = logging.getLogger(__name__)

    @router.get("/status")
    def voice_status(request: Request) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        try:
            status = voice_broker.status()
        except Exception as error:  # noqa: BLE001 - optional service boundary
            logger.exception("Voice status provider failed [%s]", request_id(request))
            raise GatewayError(
                503,
                "voice_status_unavailable",
                "voice status is temporarily unavailable",
            ) from error
        return {
            "workspace_id": principal.workspace_id,
            **normalize_json(status),
        }

    @router.post("/sessions")
    def create_voice_session(
        request: Request,
        payload: VoiceSessionRequest,
    ) -> JSONResponse:
        principal = principal_for_request(request)
        require_human(principal)
        require_workspace(services, principal, request)
        conversation = require_conversation(
            services,
            principal,
            payload.conversation_id,
        )
        try:
            details = voice_broker.create(
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
            logger.exception("Voice session creation failed [%s]", request_id(request))
            raise GatewayError(
                503,
                "voice_session_failed",
                "voice session could not be created",
            ) from error
        details_payload: Any = (
            details.to_dict() if hasattr(details, "to_dict") else details
        )
        if not isinstance(details_payload, Mapping):
            raise GatewayError(
                500,
                "voice_contract_error",
                "voice session provider returned an invalid response",
            )
        return JSONResponse(
            status_code=201,
            content=normalize_json(details_payload),
            headers={"x-request-id": request_id(request)},
        )

    return router
