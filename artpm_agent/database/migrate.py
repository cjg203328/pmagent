"""
Alembic 迁移运行器（程序化调用）
=================================

在 DatabaseManager 初始化时安全接入 Alembic 迁移：

- alembic 是受管数据库的运行时依赖；缺失时 ensure_schema 抛错
- 已有业务表但无 alembic_version：从已验证的初始基线继续执行迁移
- 全新库：执行 upgrade head 创建全部表并写入版本
- 已标记版本：upgrade head 为幂等 no-op

典型用法::

    from artpm_agent.database.migrate import ensure_schema
    ensure_schema(engine)   # engine 为 SQLAlchemy Engine
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

INITIAL_REVISION = "a177eb93a550"

# alembic 脚手架位于仓库根的 alembic/ 目录
def _alembic_dir() -> Path:
    configured = os.environ.get("ARTPM_ALEMBIC_DIR", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path.cwd() / "alembic",
        Path(__file__).resolve().parents[2] / "alembic",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "env.py").is_file():
            return candidate.resolve()
    raise RuntimeError("Alembic migration scripts are not available")


def _make_config(engine=None):
    from alembic.config import Config

    alembic_dir = _alembic_dir()
    ini_path = alembic_dir.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(alembic_dir))
    if engine is not None:
        # 让迁移作用于与 DatabaseManager 完全相同的库
        cfg.set_main_option("sqlalchemy.url", str(engine.url))
    elif os.environ.get("DATABASE_URL"):
        cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    return cfg


def _require_alembic():
    """Require Alembic before touching a managed database schema."""
    import alembic  # noqa: F401
    return True


def _metadata():
    from artpm_agent.database.models import Base

    return Base.metadata


def upgrade_head(engine=None):
    """执行迁移至 head。"""
    from alembic import command

    command.upgrade(_make_config(engine), "head")


def downgrade_base(engine=None):
    """回滚至 base（删除全部表）。"""
    from alembic import command

    command.downgrade(_make_config(engine), "base")


def stamp_head(engine=None):
    """将当前库标记为已迁移到 head（不执行任何 DDL）。"""
    from alembic import command

    command.stamp(_make_config(engine), "head")


def stamp_revision(revision: str, engine=None):
    """Mark a verified legacy schema at a specific migration baseline."""
    from alembic import command

    command.stamp(_make_config(engine), revision)


def ensure_schema(engine=None):
    """
    在 DatabaseManager 初始化时安全接入 Alembic。

    - alembic 不可用 -> 抛 ImportError
    - 已有业务表但无 alembic_version -> 从初始基线继续执行剩余迁移
    - 否则执行 upgrade head
    """
    _require_alembic()

    if engine is not None:
        from sqlalchemy import inspect

        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        has_version = "alembic_version" in existing
        has_app_tables = bool(
            existing & {table.name for table in _metadata().sorted_tables}
        )
        if not has_version and has_app_tables:
            logger.info(
                "检测到无版本标记的既有数据库，从初始基线 %s 继续迁移",
                INITIAL_REVISION,
            )
            stamp_revision(INITIAL_REVISION, engine)
            upgrade_head(engine)
            return

    upgrade_head(engine)
