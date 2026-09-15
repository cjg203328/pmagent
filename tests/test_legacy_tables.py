"""
遗留表（legacy tables）迁移测试
===========================

验证 0002 迁移把 6 张历史表（staff/quotes/reminders/operation_logs/
progress_updates/task_assignments）纳入 Alembic 管理：

1. 全新库经 ensure_schema 后，14 张表（8 ORM + 6 遗留）全部存在
2. 已存在这 6 张表的真实库场景：升级到 head 时 0002 因 checkfirst 为幂等 no-op，
   不报错、表数量不变

所有用例均使用临时库，绝不触碰真实 data/artpm.db。
"""
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Integer, String, create_engine, inspect, text

from artpm_agent.database.migrate import upgrade_head
from artpm_agent.database.models import Base, DatabaseManager


def _table_names(engine):
    return set(inspect(engine).get_table_names())


def _model_table_names():
    return {table.name for table in Base.metadata.sorted_tables}


def test_fresh_db_includes_all_14_tables(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'artpm.db'}"
    db = DatabaseManager(db_url)
    names = _table_names(db.engine)

    # 14 张表（8 ORM + 6 遗留）全部存在
    assert _model_table_names() <= names
    assert {
        "staff", "quotes", "reminders",
        "operation_logs", "progress_updates", "task_assignments",
    } <= names
    assert "alembic_version" in names


def test_legacy_tables_migration_is_idempotent_on_existing_db(tmp_path):
    """模拟真实库：14 张业务表已全部存在（schema 与 ORM 一致）+ alembic_version=0001。

    重新初始化应安全升级到 head（0002 因 checkfirst 为 no-op，不报错、不重建）。
    """
    db_url = f"sqlite:///{tmp_path / 'artpm.db'}"
    # 用 ORM 建模建出完整 14 张表，模拟真实库（schema 与 ORM 完全一致）
    legacy_engine = create_engine(db_url)
    Base.metadata.create_all(legacy_engine)
    legacy_engine.dispose()

    # 标记到 0001（模拟此前 stamp 到初始迁移）
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        ))
        conn.execute(text("INSERT INTO alembic_version VALUES ('a177eb93a550')"))
    engine.dispose()

    # 重新初始化：ensure_schema 检测到 alembic_version=0001，升级到当前 head。
    db = DatabaseManager(db_url)
    names = _table_names(db.engine)

    assert _model_table_names() <= names
    with db.engine.connect() as conn:
        version = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).fetchone()[0]
    # Head includes the tenant ownership, RLS, and legacy integer type migration.
    assert version == "d4e5f6a7b8c9"
    # 6 张遗留表仍在，未被重建
    assert {
        "staff", "quotes", "reminders",
        "operation_logs", "progress_updates", "task_assignments",
    } <= names


def test_legacy_integer_foreign_keys_upgrade_from_previous_head(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'legacy-types.db'}"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)

    # Recreate the schema shape produced before the ORM type correction.
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        for table_name, column_name in (
            ("quotes", "project_id"),
            ("reminders", "task_id"),
            ("progress_updates", "task_id"),
            ("task_assignments", "task_id"),
        ):
            with operations.batch_alter_table(table_name, recreate="always") as batch:
                batch.alter_column(
                    column_name,
                    type_=String(),
                    existing_type=Integer(),
                )
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES ('c2d3e4f50617')")
        )
    engine.dispose()

    upgraded = create_engine(db_url)
    try:
        upgrade_head(upgraded)
        columns = {
            table: {
                item["name"]: str(item["type"]).upper()
                for item in inspect(upgraded).get_columns(table)
            }
            for table in ("quotes", "reminders", "progress_updates", "task_assignments")
        }
        assert columns["quotes"]["project_id"] == "INTEGER"
        assert columns["reminders"]["task_id"] == "INTEGER"
        assert columns["progress_updates"]["task_id"] == "INTEGER"
        assert columns["task_assignments"]["task_id"] == "INTEGER"
        with upgraded.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "d4e5f6a7b8c9"
    finally:
        upgraded.dispose()
