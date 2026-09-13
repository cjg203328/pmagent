"""Canonical request scope shared by orchestration and persistence layers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ScopeError(ValueError):
    """Raised when a request scope is incomplete or malformed."""


def _value(value: Any, field: str, *, default: str | None = None) -> str:
    text = str(value if value is not None else default or "").strip()
    if not text or not _IDENTIFIER.fullmatch(text):
        raise ScopeError(f"{field} has an invalid format")
    return text


def resolve_scope(
    scope: Scope | None = None,
    *,
    tenant_id: Any = None,
    workspace_id: Any = None,
    principal_id: Any = "local-user",
    actor_role: Any = "user",
) -> Scope:
    """Resolve one scope while rejecting explicitly conflicting identities."""

    if scope is not None:
        if not isinstance(scope, Scope):
            raise ScopeError("scope must be a Scope")
        if tenant_id not in (None, "", scope.tenant_id):
            raise ScopeError("tenant_id conflicts with scope")
        if workspace_id not in (None, "", scope.workspace_id):
            raise ScopeError("workspace_id conflicts with scope")
        return scope
    return Scope(
        tenant_id=tenant_id or "local",
        workspace_id=workspace_id or "local-default",
        principal_id=principal_id or "local-user",
        actor_role=actor_role or "user",
    )


@dataclass(frozen=True, slots=True)
class Scope:
    """Immutable identity boundary for one request or conversation turn."""

    tenant_id: str = "local"
    workspace_id: str = "local-default"
    principal_id: str = "local-user"
    actor_role: str = "user"
    request_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _value(self.tenant_id, "tenant_id"))
        object.__setattr__(
            self, "workspace_id", _value(self.workspace_id, "workspace_id")
        )
        object.__setattr__(
            self, "principal_id", _value(self.principal_id, "principal_id")
        )
        role = str(self.actor_role or "").strip().casefold()
        if role not in {"user", "admin", "service"}:
            raise ScopeError("actor_role has an unsupported value")
        object.__setattr__(self, "actor_role", role)
        if self.request_id is not None:
            object.__setattr__(
                self, "request_id", _value(self.request_id, "request_id")
            )

    @property
    def actor_id(self) -> str:
        """Compatibility alias used by the harness and API adapters."""

        return self.principal_id

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "Scope":
        """Build a scope from a trusted mapping at a compatibility boundary."""

        if not isinstance(values, Mapping):
            raise ScopeError("scope source must be a mapping")

        context = values.get("tenant_context")
        if context is not None:
            return cls.from_context(context, request_id=values.get("request_id"))
        return cls(
            tenant_id=values.get("tenant_id", "local"),
            workspace_id=values.get("workspace_id", "local-default"),
            principal_id=values.get(
                "principal_id", values.get("actor_id", "local-user")
            ),
            actor_role=values.get("actor_role", "user"),
            request_id=values.get("request_id"),
        )

    @classmethod
    def from_context(cls, context: Any, *, request_id: str | None = None) -> "Scope":
        """Map a server-owned authentication context to the canonical scope."""

        roles = getattr(context, "roles", frozenset())
        actor_role = (
            "service"
            if "service" in roles
            else "admin"
            if "admin" in roles
            else "user"
        )
        return cls(
            tenant_id=getattr(context, "tenant_id", "local"),
            workspace_id=getattr(context, "workspace_id", "local-default")
            or "local-default",
            principal_id=getattr(context, "principal_id", "local-user"),
            actor_role=actor_role,
            request_id=request_id or getattr(context, "request_id", None),
        )


__all__ = ["Scope", "ScopeError", "resolve_scope"]
