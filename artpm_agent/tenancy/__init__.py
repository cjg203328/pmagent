"""Framework-neutral, request-scoped multi-tenant primitives."""

from .context import (
    TenantContext,
    TenantContextError,
    TenantContextManager,
    WorkspaceAccessDenied,
    tenant_context_from_host,
)
from .scoped_store import UnsafeStoreOperation, WorkspaceStoreGuard
from .scope import Scope, ScopeError, resolve_scope

__all__ = [
    "TenantContext",
    "TenantContextError",
    "TenantContextManager",
    "UnsafeStoreOperation",
    "WorkspaceAccessDenied",
    "WorkspaceStoreGuard",
    "Scope",
    "ScopeError",
    "resolve_scope",
    "tenant_context_from_host",
]
