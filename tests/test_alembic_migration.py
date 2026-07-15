"""
Alembic 数据层迁移测试
======================

验证：
1. 全新库经 ensure_schema 通过 Alembic 创建全部表并写入 alembic_version
2. 既有库（create_all 建立、无 alembic_version）经 ensure_schema 仅 stamp，绝不重建
3. upgrade_head / stamp_head 幂等

所有用例均使用临时库，绝不触碰真实 data/artpm.db。
"""
from sqlalchemy import create_engine, inspect, text

from artpm_agent.database.models import DatabaseManager, Base
from artpm_agent.database.migrate import stamp_head, upgrade_head


def _table_names(engine):
    return set(inspect(engine).get_table_names())


def _model_table_names():
    return {table.name for table in Base.metadata.sorted_tables}


def test_fresh_db_created_via_alembic(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'artpm.db'}"
    with DatabaseManager(db_url) as db:
        names = _table_names(db.engine)

        # 所有业务表均已创建
        assert _model_table_names() <= names
        # alembic_version 已被写入（迁移已执行）
        assert "alembic_version" in names
        with db.engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        assert row is not None and row[0]


def test_existing_legacy_db_gets_stamped_not_recreated(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'artpm.db'}"
    # 模拟“既有库”：用 create_all 建表（无 alembic_version）
    legacy = create_engine(db_url)
    Base.metadata.create_all(legacy)
    legacy_names = _table_names(legacy)
    assert _model_table_names() <= legacy_names
    assert "alembic_version" not in legacy_names
    legacy.dispose()

    # 再次初始化 DatabaseManager -> 应仅 stamp，不报错、不重建
    with DatabaseManager(db_url) as db:
        names = _table_names(db.engine)
        assert _model_table_names() <= names
        assert "alembic_version" in names
        # 业务表数量不变（仅多出 alembic_version 元数据表，未被 drop 重建）
        assert len(names) == len(legacy_names) + 1


def test_upgrade_head_and_stamp_are_idempotent(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'artpm.db'}"
    engine = create_engine(db_url)
    try:
        upgrade_head(engine)
        assert _model_table_names() <= _table_names(engine)

        # 重复 upgrade 应为 no-op
        upgrade_head(engine)
        # stamp 也应为 no-op
        stamp_head(engine)
        assert "alembic_version" in _table_names(engine)
    finally:
        engine.dispose()
