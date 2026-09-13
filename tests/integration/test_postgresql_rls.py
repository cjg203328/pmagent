"""Live PostgreSQL RLS verification.

Set ARTPM_TEST_POSTGRES_URL to a disposable database owned by a migration role.
The test creates two tenant rows and verifies each transaction sees only its
own workspace through the same SQLAlchemy model.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from artpm_agent.database.migrate import upgrade_head
from artpm_agent.database.models import DatabaseManager, Project
from artpm_agent.tenancy import TenantContext


@pytest.mark.integration
def test_postgresql_rls_blocks_cross_tenant_reads_and_writes():
    url = os.getenv("ARTPM_TEST_POSTGRES_URL", "").strip()
    admin_url = os.getenv("ARTPM_TEST_POSTGRES_ADMIN_URL", "").strip()
    if not url:
        pytest.skip("ARTPM_TEST_POSTGRES_URL is not configured")
    if admin_url:
        from sqlalchemy import create_engine

        admin_engine = create_engine(admin_url)
        try:
            upgrade_head(admin_engine)
        finally:
            admin_engine.dispose()
    manager = DatabaseManager(url)
    tenant_a = TenantContext(tenant_id="rls-a", workspace_id="workspace-a")
    tenant_b = TenantContext(tenant_id="rls-b", workspace_id="workspace-b")
    try:
        for context, name in ((tenant_a, "A"), (tenant_b, "B")):
            session = manager.get_session(context)
            try:
                session.add(Project(project_name=name, client=name, quote_amount=1.0))
                session.commit()
            finally:
                session.close()

        session = manager.get_session(tenant_a)
        try:
            assert [row.project_name for row in session.query(Project).all()] == ["A"]
            # Raw SQL bypasses SQLAlchemy's ORM loader criteria.  PostgreSQL
            # RLS must still hide the other tenant from the same connection.
            assert session.execute(
                text("SELECT project_name FROM projects ORDER BY project_name")
            ).scalars().all() == ["A"]
            with pytest.raises(DBAPIError, match="row-level security|policy|permission"):
                session.execute(
                    text(
                        "INSERT INTO projects "
                        "(project_name, client, quote_amount, created_at, tenant_id, workspace_id) "
                        "VALUES ('forged', 'x', 1, now(), 'rls-b', 'workspace-b')"
                    )
                )
                session.commit()
        finally:
            session.rollback()
            session.close()
    finally:
        manager.close()
