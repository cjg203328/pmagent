"""Add tenant ownership and PostgreSQL row-level security.

Revision ID: c2d3e4f50617
Revises: b1c2d3e4f506
"""

from alembic import op
import sqlalchemy as sa


revision = "c2d3e4f50617"
down_revision = "b1c2d3e4f506"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "projects", "assets", "team_members", "tasks", "documents",
    "knowledge_base", "deliveries", "asset_versions", "staff", "quotes",
    "reminders", "operation_logs", "progress_updates", "task_assignments",
)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for table in TENANT_TABLES:
        if table not in existing:
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        if "tenant_id" not in columns:
            op.add_column(
                table,
                sa.Column("tenant_id", sa.String(128), nullable=False, server_default="local"),
            )
        if "workspace_id" not in columns:
            op.add_column(
                table,
                sa.Column(
                    "workspace_id", sa.String(128), nullable=False,
                    server_default="local-default",
                ),
            )
        refreshed = {item["name"] for item in sa.inspect(bind).get_indexes(table)}
        for column in ("tenant_id", "workspace_id"):
            index_name = f"ix_{table}_{column}"
            if index_name not in refreshed:
                op.create_index(index_name, table, [column], unique=False)

        if bind.dialect.name == "postgresql":
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'''CREATE POLICY artpm_tenant_workspace_isolation ON "{table}"
                USING (
                    tenant_id = current_setting('app.tenant_id', true)
                    AND workspace_id = current_setting('app.workspace_id', true)
                )
                WITH CHECK (
                    tenant_id = current_setting('app.tenant_id', true)
                    AND workspace_id = current_setting('app.workspace_id', true)
                )'''
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for table in reversed(TENANT_TABLES):
        if table not in existing:
            continue
        if bind.dialect.name == "postgresql":
            op.execute(
                f'DROP POLICY IF EXISTS artpm_tenant_workspace_isolation ON "{table}"'
            )
            op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        indexes = {item["name"] for item in sa.inspect(bind).get_indexes(table)}
        columns = {item["name"] for item in sa.inspect(bind).get_columns(table)}
        for column in ("workspace_id", "tenant_id"):
            index_name = f"ix_{table}_{column}"
            if index_name in indexes:
                op.drop_index(index_name, table_name=table)
            if column in columns:
                op.drop_column(table, column)
