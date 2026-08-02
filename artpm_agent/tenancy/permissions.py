"""Framework-neutral role and permission helpers.

HTTP adapters translate these exceptions to their own 401/403 response type;
the core tenancy package never imports Flask, FastAPI, or Streamlit.
"""

from __future__ import annotations

from enum import Enum
from functools import wraps
from typing import Any, Callable, TypeVar

from .context import (
    TenantContext,
    TenantContextError,
    TenantContextManager,
    WorkspaceAccessDenied,
)


class Permission(str, Enum):
    TENANT_MANAGE = "tenant:manage"
    TENANT_VIEW = "tenant:view"
    TENANT_SETTINGS = "tenant:settings"
    WORKSPACE_CREATE = "workspace:create"
    WORKSPACE_DELETE = "workspace:delete"
    WORKSPACE_MANAGE = "workspace:manage"
    WORKSPACE_VIEW = "workspace:view"
    WORKSPACE_SETTINGS = "workspace:settings"
    PROJECT_CREATE = "project:create"
    PROJECT_UPDATE = "project:update"
    PROJECT_DELETE = "project:delete"
    PROJECT_VIEW = "project:view"
    PROJECT_ARCHIVE = "project:archive"
    USER_INVITE = "user:invite"
    USER_REMOVE = "user:remove"
    USER_MANAGE = "user:manage"
    USER_VIEW = "user:view"
    SKILL_EXECUTE = "skill:execute"
    SKILL_MANAGE = "skill:manage"
    SKILL_VIEW = "skill:view"
    DATA_READ = "data:read"
    DATA_WRITE = "data:write"
    DATA_DELETE = "data:delete"
    DATA_EXPORT = "data:export"
    AUDIT_VIEW = "audit:view"


class WorkspaceRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


ROLE_PERMISSIONS: dict[WorkspaceRole, frozenset[Permission]] = {
    WorkspaceRole.OWNER: frozenset(Permission),
    WorkspaceRole.ADMIN: frozenset(
        {
            Permission.WORKSPACE_VIEW,
            Permission.WORKSPACE_SETTINGS,
            Permission.PROJECT_CREATE,
            Permission.PROJECT_UPDATE,
            Permission.PROJECT_VIEW,
            Permission.PROJECT_ARCHIVE,
            Permission.USER_INVITE,
            Permission.USER_VIEW,
            Permission.SKILL_EXECUTE,
            Permission.SKILL_MANAGE,
            Permission.SKILL_VIEW,
            Permission.DATA_READ,
            Permission.DATA_WRITE,
            Permission.DATA_EXPORT,
        }
    ),
    WorkspaceRole.MEMBER: frozenset(
        {
            Permission.WORKSPACE_VIEW,
            Permission.PROJECT_VIEW,
            Permission.PROJECT_UPDATE,
            Permission.USER_VIEW,
            Permission.SKILL_EXECUTE,
            Permission.SKILL_VIEW,
            Permission.DATA_READ,
            Permission.DATA_WRITE,
        }
    ),
    WorkspaceRole.VIEWER: frozenset(
        {
            Permission.WORKSPACE_VIEW,
            Permission.PROJECT_VIEW,
            Permission.USER_VIEW,
            Permission.SKILL_VIEW,
            Permission.DATA_READ,
        }
    ),
}


class TenantAuthenticationRequired(TenantContextError):
    """Raised when a protected operation has no active context."""


class PermissionChecker:
    @staticmethod
    def current() -> TenantContext:
        context = TenantContextManager.get_current()
        if context is None:
            raise TenantAuthenticationRequired("tenant context is required")
        return context

    @staticmethod
    def get_user_permissions(
        user_id: Any,
        workspace_id: Any,
        *,
        context: TenantContext | None = None,
    ) -> frozenset[Permission]:
        context = context or PermissionChecker.current()
        if str(user_id) != context.principal_id:
            return frozenset()
        context.require_workspace(workspace_id)
        explicit = {
            Permission(value)
            for value in context.permissions
            if value in Permission._value2member_map_
        }
        if explicit:
            return frozenset(explicit)
        if "admin" in context.roles:
            return ROLE_PERMISSIONS[WorkspaceRole.OWNER]
        return ROLE_PERMISSIONS[WorkspaceRole.MEMBER]

    @staticmethod
    def has_permission(
        user_id: Any,
        workspace_id: Any,
        permission: Permission,
        *,
        context: TenantContext | None = None,
    ) -> bool:
        return permission in PermissionChecker.get_user_permissions(
            user_id,
            workspace_id,
            context=context,
        )

    @staticmethod
    def has_role(
        user_id: Any,
        workspace_id: Any,
        role: WorkspaceRole,
        *,
        context: TenantContext | None = None,
    ) -> bool:
        context = context or PermissionChecker.current()
        if str(user_id) != context.principal_id:
            return False
        context.require_workspace(workspace_id)
        if role in {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}:
            return "admin" in context.roles
        if role == WorkspaceRole.MEMBER:
            return bool(context.roles & {"user", "admin"})
        return True


F = TypeVar("F", bound=Callable[..., Any])


def require_permission(*permissions: Permission) -> Callable[[F], F]:
    required = frozenset(permissions)

    def decorator(function: F) -> F:
        @wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = PermissionChecker.current()
            available = PermissionChecker.get_user_permissions(
                context.principal_id,
                context.workspace_id,
                context=context,
            )
            missing = required - available
            if missing:
                raise WorkspaceAccessDenied(
                    "missing permissions: "
                    + ", ".join(sorted(item.value for item in missing))
                )
            return function(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def require_role(*roles: WorkspaceRole) -> Callable[[F], F]:
    accepted = frozenset(roles)

    def decorator(function: F) -> F:
        @wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = PermissionChecker.current()
            if not any(
                PermissionChecker.has_role(
                    context.principal_id,
                    context.workspace_id,
                    role,
                    context=context,
                )
                for role in accepted
            ):
                raise WorkspaceAccessDenied("required workspace role is missing")
            return function(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def require_tenant_owner(function: F) -> F:
    return require_role(WorkspaceRole.OWNER)(function)


__all__ = [
    "Permission",
    "PermissionChecker",
    "ROLE_PERMISSIONS",
    "TenantAuthenticationRequired",
    "WorkspaceRole",
    "require_permission",
    "require_role",
    "require_tenant_owner",
]
