"""
Alembic 环境 - ArtPM Agent
==========================

让 ``artpm_agent`` 成为顶层包（core / database / skills / utils / memory 均可直接
import），并以 ``database.models.Base.metadata`` 作为迁移的 target_metadata。

库连接URL优先级：环境变量 DATABASE_URL > alembic.ini 中的 sqlalchemy.url >
默认 <repo>/data/artpm.db。
"""
import os
import sys
from logging.config import fileConfig

from alembic import context

# 将 artpm_agent 加入 sys.path，使 database / core / skills 等顶层包可 import
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "artpm_agent"))

from database.models import Base  # noqa: E402

config = context.config

# 兜底默认库路径（与 DatabaseManager 默认一致）
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{os.path.join(REPO_ROOT, 'data', 'artpm.db')}",
    )

if config.config_file_name:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        # 日志配置缺失时不影响迁移执行
        pass

target_metadata = Base.metadata

# ``workbench_snapshot`` is a deliberately unmanaged compatibility table used
# by the legacy workspace store.  It predates Alembic and is not represented
# by the relational ORM metadata, so autogenerate must not propose dropping it
# every time ``alembic check`` runs.
_UNMANAGED_COMPAT_TABLES = frozenset({"workbench_snapshot"})


def include_object(object_, name, type_, reflected, compare_to):
    if (
        type_ == "table"
        and reflected
        and compare_to is None
        and name in _UNMANAGED_COMPAT_TABLES
    ):
        return False
    return True


def get_url() -> str:
    """解析数据库连接 URL。"""
    return os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL，不连接数据库。"""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库并执行迁移。"""
    from sqlalchemy import create_engine, pool

    connectable = create_engine(get_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
