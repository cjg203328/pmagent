"""Add the single-source rate fields to team_members and project payment terms.

领域数据契约 §10 第 1 步：`team_members` 才是有数据的表，费率必须搬到这里，
否则报价读硬编码、派单读 `team_members`，两边永远不是同一批人。

新增列全部可空，既有行保持 NULL，不使用 server_default 冒充真实费率——
`NULL` 表示「未标定」，由领域数据契约 §6 决定返回 `needs_input` 还是标注 `default`。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


# (column, type) — 顺序与领域数据契约 §2 一致
TEAM_MEMBER_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("daily_cost", sa.Float()),
    ("cost_source", sa.String(20)),
    ("cost_effective_at", sa.DateTime()),
    ("capacity_days_per_month", sa.Float()),
    ("max_concurrent_tasks", sa.Integer()),
    ("contact_wecom", sa.String(100)),
)

PROJECT_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("payment_terms_days", sa.Integer()),
)


def _add_missing(table_name: str, columns) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in set(inspector.get_table_names()):
        return
    existing = {item["name"] for item in inspector.get_columns(table_name)}
    for column_name, column_type in columns:
        if column_name in existing:
            continue
        op.add_column(table_name, sa.Column(column_name, column_type, nullable=True))


def upgrade() -> None:
    _add_missing("team_members", TEAM_MEMBER_COLUMNS)
    _add_missing("projects", PROJECT_COLUMNS)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name, columns in (
        ("projects", PROJECT_COLUMNS),
        ("team_members", TEAM_MEMBER_COLUMNS),
    ):
        if table_name not in existing_tables:
            continue
        present = {item["name"] for item in sa.inspect(bind).get_columns(table_name)}
        for column_name, _ in reversed(columns):
            if column_name in present:
                op.drop_column(table_name, column_name)
