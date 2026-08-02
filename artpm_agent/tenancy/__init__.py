"""Framework-neutral, request-scoped multi-tenant primitives."""

from .context import (
    TenantContext,
    TenantContextError,
    TenantContextManager,
    WorkspaceAccessDenied,
    tenant_context_from_host,
)
from .scoped_store import UnsafeStoreOperation, WorkspaceStoreGuard

__all__ = [
    "TenantContext",
    "TenantContextError",
    "TenantContextManager",
    "UnsafeStoreOperation",
    "WorkspaceAccessDenied",
    "WorkspaceStoreGuard",
    "tenant_context_from_host",
]
