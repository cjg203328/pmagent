"""Public REST gateway package.

Use ``create_app()`` for an isolated application instance, or run
``uvicorn artpm_agent.api:create_app --factory`` in a deployment.
"""

from .app import API_VERSION, GatewayError, create_app
from .services import (
    ChatCommand,
    ChatOutcome,
    GatewayServiceError,
    GatewayServices,
    IdentityError,
    RequestPrincipal,
    TrustedHeaderIdentityResolver,
    build_default_services,
)

__all__ = [
    "API_VERSION",
    "ChatCommand",
    "ChatOutcome",
    "GatewayError",
    "GatewayServiceError",
    "GatewayServices",
    "IdentityError",
    "RequestPrincipal",
    "TrustedHeaderIdentityResolver",
    "build_default_services",
    "create_app",
]
