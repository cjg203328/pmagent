"""Align legacy foreign-key columns with the integer primary keys they reference.

The legacy tables were originally created by the SQLite helper with text
foreign keys.  The ORM model now correctly describes the referenced project
and task identifiers as integers, but an existing database at the previous
Alembic head still needs an explicit, data-safe type migration.
"""

from __future__ import annotations

from typing import Iterable

from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c2d3e4f50617"
branch_labels = None
depends_on = None


# (table, column, nullable)
LEGACY_INTEGER_COLUMNS = (
    ("quotes", "project_id", True),
    ("reminders", "task_id", True),
    ("progress_updates", "task_id", False),
    ("task_assignments", "task_id", False),
)


def _column_values(bind, table_name: str, column_name: str) -> Iterable[object]:
    table = sa.table(table_name, sa.column(column_name, sa.String()))
    return bind.execute(sa.select(table.c[column_name])).scalars()


def _validate_integer_values(bind, table_name: str, column_name: str, nullable: bool) -> None:
    invalid: list[str] = []
    for raw in _column_values(bind, table_name, column_name):
        if raw is None:
            continue
        value = str(raw).strip()
        if not value:
            if not nullable:
                invalid.append("<empty>")
            continue
        try:
            int(value, 10)
        except (TypeError, ValueError):
            invalid.append(value)
            if len(invalid) >= 5:
                break
    if invalid:
        examples = ", ".join(repr(value) for value in invalid)
        raise RuntimeError(
            f"Cannot migrate {table_name}.{column_name} to INTEGER; "
            f"non-integer legacy values: {examples}. Clean or back up the data "
            "before retrying the migration."
        )


def _normalize_nullable_empty_values(bind, table_name: str, column_name: str) -> None:
    table = sa.table(table_name, sa.column(column_name, sa.String()))
    column = table.c[column_name]
    bind.execute(
        table.update()
        .where(column.is_not(None))
        .where(sa.func.trim(column) == "")
        .values({column: None})
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name, column_name, nullable in LEGACY_INTEGER_COLUMNS:
        if table_name not in existing_tables:
            continue
        columns = {item["name"]: item for item in inspector.get_columns(table_name)}
        if column_name not in columns:
            continue
        if isinstance(columns[column_name]["type"], sa.Integer):
            continue

        _validate_integer_values(bind, table_name, column_name, nullable)
        if nullable:
            _normalize_nullable_empty_values(bind, table_name, column_name)

        if bind.dialect.name == "postgresql":
            quoted = sa.sql.quoted_name(column_name, quote=True)
            op.alter_column(
                table_name,
                column_name,
                type_=sa.Integer(),
                existing_type=sa.String(),
                existing_nullable=nullable,
                postgresql_using=(
                    f"NULLIF(trim({quoted}), '')::integer"
                    if nullable
                    else f"trim({quoted})::integer"
                ),
            )
        else:
            # SQLite requires a table rebuild for type changes.  Alembic's
            # batch operation preserves indexes, constraints, and data while
            # applying SQLite's explicit INTEGER cast during the copy.
            with op.batch_alter_table(table_name, recreate="always") as batch:
                batch.alter_column(
                    column_name,
                    type_=sa.Integer(),
                    existing_type=sa.String(),
                    existing_nullable=nullable,
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name, column_name, nullable in LEGACY_INTEGER_COLUMNS:
        if table_name not in existing_tables:
            continue
        columns = {item["name"]: item for item in inspector.get_columns(table_name)}
        if column_name not in columns:
            continue
        if isinstance(columns[column_name]["type"], sa.String):
            continue

        if bind.dialect.name == "postgresql":
            op.alter_column(
                table_name,
                column_name,
                type_=sa.String(),
                existing_type=sa.Integer(),
                existing_nullable=nullable,
                postgresql_using=f'"{column_name}"::text',
            )
        else:
            with op.batch_alter_table(table_name, recreate="always") as batch:
                batch.alter_column(
                    column_name,
                    type_=sa.String(),
                    existing_type=sa.Integer(),
                    existing_nullable=nullable,
                )
