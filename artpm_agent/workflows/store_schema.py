"""Workflow-store schema ownership and forward migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

SCHEMA_VERSION = 3

INITIAL_SCHEMA_SQL = """
CREATE TABLE workflow_definitions (
    tenant_id TEXT NOT NULL DEFAULT 'local',
    workspace_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    source TEXT NOT NULL CHECK(source IN ('builtin', 'custom')),
    read_only INTEGER NOT NULL CHECK(read_only IN (0, 1)),
    definition_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, profile_id, workflow_id, version)
);

CREATE TABLE workflow_overrides (
    tenant_id TEXT NOT NULL DEFAULT 'local',
    workspace_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version INTEGER NOT NULL,
    enabled INTEGER CHECK(enabled IN (0, 1)),
    priority INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, profile_id, workflow_id, workflow_version),
    FOREIGN KEY(workspace_id, profile_id, workflow_id, workflow_version)
        REFERENCES workflow_definitions(
            workspace_id, profile_id, workflow_id, version
        ) ON DELETE CASCADE
);

CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'local',
    workspace_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    turn_id TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'awaiting_approval', 'running',
        'succeeded', 'failed', 'cancelled'
    )),
    state_version INTEGER NOT NULL DEFAULT 0,
    current_step INTEGER NOT NULL DEFAULT 0,
    definition_snapshot_json TEXT NOT NULL,
    input_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    outputs_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(workspace_id, profile_id, idempotency_key),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE workflow_steps (
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL CHECK(step_index >= 0 AND step_index < 8),
    tenant_id TEXT NOT NULL DEFAULT 'local',
    step_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'awaiting_approval', 'running',
        'succeeded', 'failed', 'skipped'
    )),
    state_version INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    error TEXT,
    error_class TEXT,
    next_retry_at TEXT,
    compensation_skill_id TEXT,
    started_at TEXT,
    completed_at TEXT,
    PRIMARY KEY(run_id, step_index),
    FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
);

CREATE TABLE workflow_approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'local',
    requirement TEXT NOT NULL CHECK(requirement IN ('none', 'user', 'admin')),
    status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected')),
    actor TEXT,
    actor_level TEXT CHECK(actor_level IN ('none', 'user', 'admin')),
    note TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    UNIQUE(run_id, step_index, requirement),
    FOREIGN KEY(run_id, step_index)
        REFERENCES workflow_steps(run_id, step_index) ON DELETE CASCADE
);

CREATE TABLE workflow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'local',
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
);

CREATE INDEX idx_workflow_runs_conversation
    ON workflow_runs(conversation_id, created_at DESC);
CREATE INDEX idx_workflow_runs_status
    ON workflow_runs(status, updated_at);
CREATE INDEX idx_workflow_events_run
    ON workflow_events(run_id, id);
"""


def migrate_tenant_columns(conn: sqlite3.Connection) -> None:
    """Add tenant ownership to databases created by schema version 1."""

    for table in (
        "workflow_definitions",
        "workflow_overrides",
        "workflow_runs",
        "workflow_steps",
        "workflow_approvals",
        "workflow_events",
    ):
        columns = {
            str(item[1]) for item in conn.execute(f"PRAGMA table_info({table})")
        }
        if "tenant_id" not in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN tenant_id "
                "TEXT NOT NULL DEFAULT 'local'"
            )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_definitions_tenant "
        "ON workflow_definitions(tenant_id, workspace_id, profile_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_runs_tenant "
        "ON workflow_runs(tenant_id, workspace_id, profile_id, created_at DESC)"
    )
    has_workspaces = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspaces'"
    ).fetchone()
    if has_workspaces is None:
        return
    conn.execute(
        "UPDATE workflow_definitions SET tenant_id = COALESCE(("
        "SELECT tenant_id FROM workspaces "
        "WHERE workspaces.id = workflow_definitions.workspace_id"
        "), 'local') WHERE tenant_id = 'local'"
    )
    conn.execute(
        "UPDATE workflow_overrides SET tenant_id = COALESCE(("
        "SELECT tenant_id FROM workspaces "
        "WHERE workspaces.id = workflow_overrides.workspace_id"
        "), 'local') WHERE tenant_id = 'local'"
    )
    conn.execute(
        "UPDATE workflow_runs SET tenant_id = COALESCE(("
        "SELECT tenant_id FROM workspaces "
        "WHERE workspaces.id = workflow_runs.workspace_id"
        "), 'local') WHERE tenant_id = 'local'"
    )
    conn.execute(
        "UPDATE workflow_steps SET tenant_id = COALESCE(("
        "SELECT tenant_id FROM workflow_runs "
        "WHERE workflow_runs.id = workflow_steps.run_id"
        "), 'local') WHERE tenant_id = 'local'"
    )
    conn.execute(
        "UPDATE workflow_approvals SET tenant_id = COALESCE(("
        "SELECT tenant_id FROM workflow_runs "
        "WHERE workflow_runs.id = workflow_approvals.run_id"
        "), 'local') WHERE tenant_id = 'local'"
    )
    conn.execute(
        "UPDATE workflow_events SET tenant_id = COALESCE(("
        "SELECT tenant_id FROM workflow_runs "
        "WHERE workflow_runs.id = workflow_events.run_id"
        "), 'local') WHERE tenant_id = 'local'"
    )


def migrate_retry_columns(conn: sqlite3.Connection) -> None:
    """Add retry and recovery bookkeeping to existing workflow steps."""

    columns = {
        str(item[1]) for item in conn.execute("PRAGMA table_info(workflow_steps)")
    }
    for name, definition in (
        ("error_class", "TEXT"),
        ("next_retry_at", "TEXT"),
        ("compensation_skill_id", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE workflow_steps ADD COLUMN {name} {definition}")


def migrate_workflow_schema(
    conn: sqlite3.Connection,
    now: Callable[[], str],
    *,
    supported_version: int = SCHEMA_VERSION,
) -> None:
    """Bring one authoritative workflow database up to ``supported_version``."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM workflow_schema_migrations"
    ).fetchone()
    current_version = int(row["version"])
    if current_version > supported_version:
        raise RuntimeError(
            "Workflow database schema is newer than this application supports"
        )
    if current_version < 1:
        conn.executescript(INITIAL_SCHEMA_SQL)
        conn.execute(
            "INSERT INTO workflow_schema_migrations(version, applied_at) VALUES (?, ?)",
            (1, now()),
        )
        current_version = 1
    if current_version < 2:
        migrate_tenant_columns(conn)
        conn.execute(
            "INSERT INTO workflow_schema_migrations(version, applied_at) VALUES (?, ?)",
            (2, now()),
        )
        current_version = 2
    if current_version < 3:
        migrate_retry_columns(conn)
        conn.execute(
            "INSERT INTO workflow_schema_migrations(version, applied_at) VALUES (?, ?)",
            (3, now()),
        )


__all__ = [
    "INITIAL_SCHEMA_SQL",
    "SCHEMA_VERSION",
    "migrate_retry_columns",
    "migrate_tenant_columns",
    "migrate_workflow_schema",
]
