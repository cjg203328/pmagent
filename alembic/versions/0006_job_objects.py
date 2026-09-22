"""Job 一等公民：jobs 与 job_artifacts 两张表。

PRD §4 对象模型改为 `workspace → project → job → step → artifact`，对话降级为
Job 的一个通道。本迁移只建表，不搬数据——现有 `workflow_runs` 继续原样存在，
`Job.workflow_run_id` 是对它的引用，不复制也不改写。

关键列设计（`prototype/交互规格.md` §1）：

- `workflow_run_id` / `conversation_id` 可空：Job 必须能在无人对话时被创建、
  被定时触发、推进到完成。设成 NOT NULL 就等于把「任务为中心」退回聊天侧栏。
- `job_artifacts.version` 与 `UNIQUE(job_id, filename, version)`：成果版本**追加**，
  同一 Job 重跑不得覆盖历史成果（PRD §9）。
- `verification_status` 默认 `pending`，沿用既有核验契约，只有 `passed` 可下载。

upgrade 用 `Base.metadata.create_all(checkfirst=True)`，在已经手工建过表的库上幂等。
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

JOB_TABLES = ("jobs", "job_artifacts")


def upgrade() -> None:
    bind = op.get_bind()
    from database.models import Base

    tables = [
        Base.metadata.tables[name]
        for name in JOB_TABLES
        if name in Base.metadata.tables
    ]
    Base.metadata.create_all(bind, tables=tables, checkfirst=True)

    inspector = inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes("jobs")}
    for name, column in (
        ("ix_jobs_status_created", "status"),
        ("ix_jobs_project_status", "project_id"),
    ):
        if name not in existing_indexes:
            op.create_index(name, "jobs", [column])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    for name in reversed(JOB_TABLES):
        if name in existing:
            op.drop_table(name)
