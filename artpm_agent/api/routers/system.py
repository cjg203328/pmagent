"""Liveness and readiness routes with no Agent/model initialization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from artpm_agent.api.services import GatewayServices


class VoiceStatusPort(Protocol):
    def status(self) -> Mapping[str, object]: ...


def create_system_router(
    *,
    services: GatewayServices,
    api_version: str,
    request_id: Callable[[Request], str],
    normalize_json: Callable[[object], object],
    voice_broker: VoiceStatusPort,
    logger: logging.Logger,
) -> APIRouter:
    router = APIRouter()

    @router.get("/", tags=["system"])
    def root(request: Request) -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={
                "service": "artpm-agent-api",
                "version": api_version,
                "status": "ok",
                "message": "ArtPM Agent API is running",
                "links": {
                    "health": "/health",
                    "ready": "/ready",
                    "docs": "/docs",
                    "openapi": "/openapi.json",
                },
            },
            headers={"x-request-id": request_id(request)},
        )

    def health_response(request: Request, *, readiness: bool) -> JSONResponse:
        try:
            result = services.health()
        except Exception:  # noqa: BLE001 - health must remain serializable
            logger.exception("API health check failed")
            result = {
                "status": "unhealthy",
                "checks": {"gateway": {"status": "error"}},
            }
        try:
            result.setdefault("optional", {})["voice"] = normalize_json(
                voice_broker.status()
            )
        except Exception:  # noqa: BLE001 - optional channel never blocks core
            result.setdefault("optional", {})["voice"] = {
                "enabled": False,
                "realtime_ready": False,
                "status": "unavailable",
            }
        result.update({"service": "artpm-agent-api", "version": api_version})
        if readiness:
            result["ready"] = result.get("status") == "ok"
            status_code = 200 if result["ready"] else 503
        else:
            status_code = 200 if result.get("status") in {"ok", "degraded"} else 503
        return JSONResponse(
            status_code=status_code,
            content=normalize_json(result),
            headers={"x-request-id": request_id(request)},
        )

    @router.get("/health", tags=["system"])
    def health(request: Request) -> JSONResponse:
        return health_response(request, readiness=False)

    @router.get("/ready", tags=["system"])
    def ready(request: Request) -> JSONResponse:
        return health_response(request, readiness=True)

    return router


__all__ = ["VoiceStatusPort", "create_system_router"]
