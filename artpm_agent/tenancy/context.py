"""Server-owned tenant context shared by API, UI, Skills, and stores."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import re
from typing import Any, Iterator


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class TenantContextError(ValueError):
    """Raised when a host supplies an invalid or forged tenant context."""


class WorkspaceAccessDenied(TenantContextError):
    """Raised when an operation targets a different workspace."""


def _identifier(value: Any, field_name: str, *, optional: bool = False) -> str:
    text = str(value or "").strip()
    if optional and not text:
        return ""
    if not text or not _IDENTIFIER.fullmatch(text):
        raise TenantContextError(f"{field_name} has an invalid format")
    return text


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Immutable identity scope created only by a trusted application host."""

    tenant_id: str
    workspace_id: str = ""
    principal_id: str = "local-user"
    roles: frozenset[str] = field(default_factory=lambda: frozenset({"user"}))
    request_id: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _identifier(self.tenant_id, "tenant_id"))
        object.__setattr__(
            self,
            "workspace_id",
            _identifier(self.workspace_id, "workspace_id", optional=True),
        )
        object.__setattr__(
            self,
            "principal_id",
            _identifier(self.principal_id, "principal_id"),
        )
        roles = frozenset(
            str(role).strip().casefold() for role in self.roles if str(role).strip()
        )
        if not roles or not roles.issubset({"user", "admin", "service"}):
            raise TenantContextError("roles contain an unsupported value")
        object.__setattr__(self, "roles", roles)
        permissions = frozenset(
            str(permission).strip()
            for permission in self.permissions
            if str(permission).strip()
        )
        object.__setattr__(self, "permissions", permissions)
        if self.request_id is not None:
            object.__setattr__(
                self,
                "request_id",
                _identifier(self.request_id, "request_id"),
            )

    @classmethod
    def local(cls) -> "TenantContext":
        return cls(
            tenant_id="local",
            workspace_id="local-default",
            principal_id="local-user",
            roles=frozenset({"user"}),
        )

    @property
    def user_id(self) -> str:
        """Compatibility alias for hosts that call the principal a user."""

        return self.principal_id

    def require_workspace(self, requested: Any = None) -> str:
        if not self.workspace_id:
            raise WorkspaceAccessDenied("workspace scope is required")
        if requested is None or str(requested).strip() == "":
            return self.workspace_id
        candidate = _identifier(requested, "workspace_id")
        if candidate != self.workspace_id:
            raise WorkspaceAccessDenied(
                "workspace does not match the authenticated context"
            )
        return self.workspace_id

    def bind_inputs(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        """Bind server-owned scope and reject model/client attempts to replace it."""

        if not isinstance(inputs, Mapping):
            raise TenantContextError("skill inputs must be a mapping")
        bound = dict(inputs)
        requested_tenant = bound.get("tenant_id")
        if requested_tenant is not None and str(requested_tenant).strip() not in {
            "",
            self.tenant_id,
        }:
            raise WorkspaceAccessDenied(
                "tenant does not match the authenticated context"
            )
        requested_workspace = bound.get("workspace_id")
        self.require_workspace(requested_workspace)
        requested_principal = bound.pop("principal_id", None)
        if requested_principal is not None and str(requested_principal).strip() not in {
            "",
            self.principal_id,
        }:
            raise WorkspaceAccessDenied(
                "principal does not match the authenticated context"
            )
        bound["tenant_id"] = self.tenant_id
        bound["workspace_id"] = self.workspace_id
        return bound


_CURRENT: ContextVar[TenantContext | None] = ContextVar(
    "artpm_tenant_context", default=None
)


class TenantContextManager:
    """Compatibility facade over request-safe ``contextvars`` storage."""

    @staticmethod
    def get_current() -> TenantContext | None:
        return _CURRENT.get()

    @staticmethod
    def set_current(context: TenantContext) -> Token[TenantContext | None]:
        if not isinstance(context, TenantContext):
            raise TenantContextError("context must be a TenantContext")
        return _CURRENT.set(context)

    @staticmethod
    def reset(token: Token[TenantContext | None]) -> None:
        _CURRENT.reset(token)

    @staticmethod
    @contextmanager
    def use(context: TenantContext) -> Iterator[TenantContext]:
        token = TenantContextManager.set_current(context)
        try:
            yield context
        finally:
            TenantContextManager.reset(token)


def tenant_context_from_host(host: Mapping[str, Any] | None) -> TenantContext | None:
    """Read a server-created context and fail closed on mappings or conflicts."""

    if not isinstance(host, Mapping):
        return None
    value = host.get("tenant_context")
    if value is None:
        return None
    if not isinstance(value, TenantContext):
        raise TenantContextError(
            "tenant_context must be a server-created TenantContext instance"
        )
    if host.get("tenant_id") not in {None, "", value.tenant_id}:
        raise WorkspaceAccessDenied("host tenant scope conflicts with tenant_context")
    value.require_workspace(host.get("workspace_id"))
    return value


__all__ = [
    "TenantContext",
    "TenantContextError",
    "TenantContextManager",
    "WorkspaceAccessDenied",
    "tenant_context_from_host",
]
