"""Bind trusted tenant identity to every PostgreSQL transaction."""

from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.orm import with_loader_criteria

from artpm_agent.tenancy import TenantContext, TenantContextManager


def _context_for(session) -> TenantContext:
    context = session.info.get("tenant_context") or TenantContextManager.get_current()
    return context if isinstance(context, TenantContext) else TenantContext.local()


def install_tenant_session_hooks(session_factory) -> None:
    """Install one idempotent transaction hook on a session factory."""
    if getattr(session_factory, "_artpm_rls_installed", False):
        return

    @event.listens_for(session_factory, "after_begin")
    def _set_rls_context(session, _transaction, connection) -> None:
        if connection.dialect.name != "postgresql":
            return
        context = _context_for(session)
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": context.tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.workspace_id', :workspace, true)"),
            {"workspace": context.workspace_id},
        )

    @event.listens_for(session_factory, "before_flush")
    def _bind_new_rows(session, _flush_context, _instances) -> None:
        context = _context_for(session)
        for instance in session.new:
            if not hasattr(instance, "tenant_id") or not hasattr(instance, "workspace_id"):
                continue
            tenant_id = getattr(instance, "tenant_id", None)
            workspace_id = getattr(instance, "workspace_id", None)
            if tenant_id not in {None, "", "local", context.tenant_id}:
                raise ValueError("new row tenant_id conflicts with session tenant")
            if workspace_id not in {None, "", "local-default", context.workspace_id}:
                raise ValueError("new row workspace_id conflicts with session workspace")
            instance.tenant_id = context.tenant_id
            instance.workspace_id = context.workspace_id

    @event.listens_for(session_factory, "do_orm_execute")
    def _scope_orm_statements(orm_execute_state) -> None:
        """Apply the trusted scope to every ORM read in this session.

        PostgreSQL RLS remains the deployment-level backstop, but SQLite is the
        default local backend and has no equivalent policy.  Applying the
        criteria at the Session boundary keeps legacy CRUD methods safe without
        relying on every caller to remember a tenant predicate.
        """

        if not (
            orm_execute_state.is_select
            or orm_execute_state.is_update
            or orm_execute_state.is_delete
        ):
            return
        if orm_execute_state.execution_options.get("skip_tenant_scope"):
            return

        from artpm_agent.database.models import TenantScopedMixin

        context = _context_for(orm_execute_state.session)
        tenant_id = context.tenant_id
        workspace_id = context.workspace_id
        orm_execute_state.statement = orm_execute_state.statement.options(
            with_loader_criteria(
                TenantScopedMixin,
                lambda model: (
                    (model.tenant_id == tenant_id)
                    & (model.workspace_id == workspace_id)
                ),
                include_aliases=True,
            )
        )

    session_factory._artpm_rls_installed = True


__all__ = ["install_tenant_session_hooks"]
