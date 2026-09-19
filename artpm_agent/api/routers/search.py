"""Knowledge search routes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

from ..errors import GatewayError
from ..models import KnowledgeSearchRequest
from ..services import GatewayServices, GatewayServiceError, RequestPrincipal

if TYPE_CHECKING:
    from collections.abc import Callable


def create_search_router(
    *,
    services: GatewayServices,
    principal_for_request: Callable[[Request], RequestPrincipal],
    require_workspace: Callable[[GatewayServices, RequestPrincipal, Request], None],
    request_id: Callable[[Request], str],
    normalize_json: Callable[[Any], Any],
) -> APIRouter:
    """Create knowledge search router with injected dependencies."""
    router = APIRouter(prefix="/v1", tags=["knowledge"])
    logger = logging.getLogger(__name__)

    @router.post("/search")
    def search_knowledge(
        request: Request,
        payload: KnowledgeSearchRequest,
    ) -> dict[str, object]:
        """Search only the current workspace knowledge scope."""
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        handler = services.knowledge_search_handler
        if handler is None:
            raise GatewayError(
                503,
                "knowledge_search_unavailable",
                "workspace knowledge retrieval is unavailable",
            )
        try:
            items = handler(principal, payload.model_dump(mode="json"))
        except GatewayServiceError as error:
            raise GatewayError(
                503, "knowledge_search_unavailable", str(error)
            ) from error
        except (ValueError, TypeError) as error:
            raise GatewayError(400, "invalid_search_request", str(error)) from error
        except Exception as error:  # noqa: BLE001 - normalize provider errors
            logger.exception("Knowledge search failed [%s]", request_id(request))
            raise GatewayError(
                503, "knowledge_search_failed", "knowledge search could not complete"
            ) from error
        return {
            "workspace_id": principal.workspace_id,
            "query": payload.query,
            "items": normalize_json(items),
        }

    return router
