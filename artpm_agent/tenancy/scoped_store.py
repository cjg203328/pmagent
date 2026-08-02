"""Generic workspace guard for stores that expose explicit workspace arguments."""

from __future__ import annotations

import inspect
from typing import Any

from .context import TenantContext, WorkspaceAccessDenied


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
        return method(*args, **kwargs)
