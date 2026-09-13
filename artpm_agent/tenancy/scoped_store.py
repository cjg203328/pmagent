"""Generic workspace guard for stores that expose explicit workspace arguments."""

from __future__ import annotations

import inspect
from typing import Any

from .context import (
    TenantContext,
    TenantContextManager,
    WorkspaceAccessDenied,
)


class UnsafeStoreOperation(WorkspaceAccessDenied):
    """Raised when a store method cannot prove workspace isolation."""


class WorkspaceStoreGuard:
    """Invoke only store methods with an explicit ``workspace_id`` boundary.

    The context workspace must already be a globally unique storage ID whose
    tenant membership was authenticated by the ingress layer.
    """

    def __init__(self, store: Any, tenant_context: TenantContext):
        self._store = store
        self.context = tenant_context

    def call(self, method_name: str, /, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(method_name, str) or method_name.startswith("_"):
            raise UnsafeStoreOperation("private store operations are not available")
        method = getattr(self._store, method_name, None)
        if not callable(method):
            raise AttributeError(method_name)
        signature = inspect.signature(method)
        workspace_parameter = signature.parameters.get("workspace_id")
        if workspace_parameter is None:
            raise UnsafeStoreOperation(
                f"store method {method_name!r} has no workspace_id boundary"
            )
        requested = kwargs.get("workspace_id")
        kwargs["workspace_id"] = self.context.require_workspace(requested)
        tenant_parameter = signature.parameters.get("tenant_id")
        if tenant_parameter is not None or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ):
            requested_tenant = kwargs.get("tenant_id")
            if requested_tenant not in {None, "", self.context.tenant_id}:
                raise WorkspaceAccessDenied(
                    "tenant_id does not match the authenticated context"
                )
            kwargs["tenant_id"] = self.context.tenant_id
        current = TenantContextManager.get_current()
        if current is not None and current != self.context:
            raise WorkspaceAccessDenied(
                "store guard context conflicts with authenticated context"
            )
        with TenantContextManager.use(self.context):
            return method(*args, **kwargs)
