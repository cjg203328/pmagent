"""Composable FastAPI routers for the public gateway."""

from .capabilities import create_capabilities_router
from .chat import create_chat_router
from .permissions import create_permissions_router
from .search import create_search_router
from .system import create_system_router
from .voice import create_voice_router
from .workflows import create_workflows_router
from .workspaces import create_workspaces_router

__all__ = [
    "create_capabilities_router",
    "create_chat_router",
    "create_permissions_router",
    "create_search_router",
    "create_system_router",
    "create_voice_router",
    "create_workflows_router",
    "create_workspaces_router",
]
