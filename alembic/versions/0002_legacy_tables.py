"""legacy tables (staff/quotes/reminders/operation_logs/progress_updates/task_assignments)

Revision ID: b1c2d3e4f506
Revises: a177eb93a550
Create Date: 2026-07-14

这些表历史上由 memory/sqlite_manager.py 以原生 SQL 维护，未被 ORM 建模，导致
Alembic 无法覆盖、产生 schema 漂移。本迁移将其纳入 Alembic 管理。

upgrade 使用 ``Base.metadata.create_all(checkfirst=True)``，保证在已存在这些表的
真实库上幂等（不会因重复建表而报错）；全新库则正常创建。
"""
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'b1c2d3e4f506'
down_revision = 'a177eb93a550'
branch_labels = None
depends_on = None

LEGACY_TABLES = (
    "staff",
    "quotes",
    "reminders",
    "operation_logs",
    "progress_updates",
    "task_assignments",
)


def upgrade():
    bind = op.get_bind()
    from database.models import Base

    tables = [Base.metadata.tables[n] for n in LEGACY_TABLES if n in Base.metadata.tables]
    # checkfirst=True：已含这些表的真实库上不会重复建表（幂等、零风险）
    Base.metadata.create_all(bind, tables=tables, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    for name in reversed(LEGACY_TABLES):
        if name in existing:
            op.drop_table(name)
