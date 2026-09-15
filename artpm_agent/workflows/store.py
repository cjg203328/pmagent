"""SQLite persistence for immutable definitions and resumable workflow runs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from .defaults import get_builtin_workflows
from .models import (
    APPROVAL_RANK,
    ApprovalRequirement,
    ApprovalStatus,
    RetryErrorClass,
    RunStatus,
    WorkflowApproval,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowOverride,
    WorkflowRun,
    WorkflowStepRun,
)
from .risk_policy import DEFAULT_RISK_POLICY
from artpm_agent.tenancy.scope import Scope


class WorkflowConflictError(RuntimeError):
    """Raised when a compare-and-swap transition loses a race."""


class WorkflowStore:
    """Own workflow tables in the same SQLite database as conversations."""

    SCHEMA_VERSION = 3
    BUSY_TIMEOUT_MS = 10_000
    MAX_JSON_BYTES = 256 * 1024

    def __init__(
        self,
        db_path: str | Path,
        *,
        install_builtins: bool = True,
    ) -> None:
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        self._enable_wal()
        self._migrate()
        if install_builtins:
            self.ensure_builtins()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {self.BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA synchronous = NORMAL")
            return conn
        except BaseException:
            conn.close()
            raise

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = None
        try:
            conn = self._connect()
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except Exception:
            if conn is not None and conn.in_transaction:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                conn.close()

    def _enable_wal(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @classmethod
    def _json(cls, value: Any) -> str:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if len(raw.encode("utf-8")) > cls.MAX_JSON_BYTES:
            raise ValueError(
                f"workflow JSON payload exceeds {cls.MAX_JSON_BYTES} bytes"
            )
        return raw

    @classmethod
    def _definition_json(cls, definition: WorkflowDefinition) -> str:
        return cls._json(definition.model_dump(mode="json"))

    @classmethod
    def _definition_checksum(cls, definition: WorkflowDefinition) -> str:
        return hashlib.sha256(
            cls._definition_json(definition).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _loads(raw: str | None, fallback: Any) -> Any:
        if not raw:
            return fallback
        return json.loads(raw)

    def _migrate(self) -> None:
        with self._connection(write=True) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version "
                "FROM workflow_schema_migrations"
            ).fetchone()
            current_version = int(row["version"])
            if current_version > self.SCHEMA_VERSION:
                raise RuntimeError(
                    "Workflow database schema is newer than this application supports"
                )
            if current_version < 1:
                conn.executescript(
                    """
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
                    PRIMARY KEY(
                        workspace_id, profile_id, workflow_id, workflow_version
                    ),
                    FOREIGN KEY(
                        workspace_id, profile_id, workflow_id, workflow_version
                    ) REFERENCES workflow_definitions(
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
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                        ON DELETE CASCADE
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
                )
                conn.execute(
                    "INSERT INTO workflow_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, self._now()),
                )
                current_version = 1

            if current_version < 2:
                self._migrate_tenant_columns(conn)
                conn.execute(
                    "INSERT INTO workflow_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (2, self._now()),
                )
                current_version = 2

            if current_version < 3:
                self._migrate_retry_columns(conn)
                conn.execute(
                    "INSERT INTO workflow_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (3, self._now()),
                )

    @staticmethod
    def _migrate_tenant_columns(conn: sqlite3.Connection) -> None:
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
                    f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'local'"
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
            "SELECT tenant_id FROM workspaces WHERE workspaces.id = workflow_definitions.workspace_id"
            "), 'local') WHERE tenant_id = 'local'"
        )
        conn.execute(
            "UPDATE workflow_overrides SET tenant_id = COALESCE(("
            "SELECT tenant_id FROM workspaces WHERE workspaces.id = workflow_overrides.workspace_id"
            "), 'local') WHERE tenant_id = 'local'"
        )
        conn.execute(
            "UPDATE workflow_runs SET tenant_id = COALESCE(("
            "SELECT tenant_id FROM workspaces WHERE workspaces.id = workflow_runs.workspace_id"
            "), 'local') WHERE tenant_id = 'local'"
        )
        conn.execute(
            "UPDATE workflow_steps SET tenant_id = COALESCE(("
            "SELECT tenant_id FROM workflow_runs WHERE workflow_runs.id = workflow_steps.run_id"
            "), 'local') WHERE tenant_id = 'local'"
        )
        conn.execute(
            "UPDATE workflow_approvals SET tenant_id = COALESCE(("
            "SELECT tenant_id FROM workflow_runs WHERE workflow_runs.id = workflow_approvals.run_id"
            "), 'local') WHERE tenant_id = 'local'"
        )
        conn.execute(
            "UPDATE workflow_events SET tenant_id = COALESCE(("
            "SELECT tenant_id FROM workflow_runs WHERE workflow_runs.id = workflow_events.run_id"
            "), 'local') WHERE tenant_id = 'local'"
        )

    @staticmethod
    def _migrate_retry_columns(conn: sqlite3.Connection) -> None:
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
                conn.execute(
                    f"ALTER TABLE workflow_steps ADD COLUMN {name} {definition}"
                )

    @staticmethod
    def _scope_values(
        *,
        scope: Scope | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        default_workspace: str = "local-default",
    ) -> tuple[str, str]:
        if scope is not None:
            if not isinstance(scope, Scope):
                raise TypeError("scope must be a Scope")
            if tenant_id not in (None, "", scope.tenant_id):
                raise ValueError("tenant_id conflicts with scope")
            if workspace_id not in (None, default_workspace, scope.workspace_id):
                raise ValueError("workspace_id conflicts with scope")
            return scope.tenant_id, scope.workspace_id
        return tenant_id or "local", workspace_id or default_workspace

    def tenant_for_workspace(self, workspace_id: str) -> str:
        """Return the persisted tenant for a workspace, or the local default."""

        with self._connection() as conn:
            has_workspaces = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'workspaces'"
            ).fetchone()
            if has_workspaces is None:
                return "local"
            row = conn.execute(
                "SELECT tenant_id FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
        if row is None or not str(row["tenant_id"] or "").strip():
            return "local"
        return str(row["tenant_id"])

    @staticmethod
    def _scope_filter(
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        scope: Scope | None = None,
        alias: str = "",
    ) -> tuple[str, tuple[Any, ...]]:
        if scope is not None:
            if not isinstance(scope, Scope):
                raise TypeError("scope must be a Scope")
            if tenant_id not in (None, "", scope.tenant_id):
                raise ValueError("tenant_id conflicts with scope")
            if workspace_id not in (None, "", scope.workspace_id):
                raise ValueError("workspace_id conflicts with scope")
            tenant_id = scope.tenant_id
            workspace_id = scope.workspace_id
        prefix = f"{alias}." if alias else ""
        clauses: list[str] = []
        parameters: list[Any] = []
        if tenant_id is not None:
            clauses.append(f" AND {prefix}tenant_id = ?")
            parameters.append(tenant_id)
        if workspace_id is not None:
            clauses.append(f" AND {prefix}workspace_id = ?")
            parameters.append(workspace_id)
        return "".join(clauses), tuple(parameters)

    def _mutation_scope(
        self,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        scope: Scope | None = None,
    ) -> tuple[str, str | None]:
        """Resolve the identity filter used by workflow state mutations."""

        if scope is not None:
            return self._scope_values(
                scope=scope,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        if tenant_id is None:
            tenant_id = (
                self.tenant_for_workspace(workspace_id)
                if workspace_id is not None
                else "local"
            )
        return tenant_id, workspace_id

    @staticmethod
    def _validate_workspace_tenant(
        conn: sqlite3.Connection,
        workspace_id: str,
        tenant_id: str,
    ) -> None:
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspaces'"
            ).fetchone()
            is None
        ):
            return
        row = conn.execute(
            "SELECT tenant_id FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if row is not None and row["tenant_id"] not in (None, "", tenant_id):
            raise ValueError("workspace does not belong to the requested tenant")

    def _put_definition_locked(
        self,
        conn: sqlite3.Connection,
        definition: WorkflowDefinition,
    ) -> WorkflowDefinition:
        """Install one definition while the caller owns the write transaction."""

        raw = self._definition_json(definition)
        checksum = self._definition_checksum(definition)
        self._validate_workspace_tenant(
            conn, definition.workspace_id, definition.tenant_id
        )
        existing = conn.execute(
            """
            SELECT checksum FROM workflow_definitions
            WHERE tenant_id = ? AND workspace_id = ? AND profile_id = ?
              AND workflow_id = ? AND version = ?
            """,
            (
                definition.tenant_id,
                definition.workspace_id,
                definition.profile_id,
                definition.id,
                definition.version,
            ),
        ).fetchone()
        if existing is not None:
            if existing["checksum"] != checksum:
                raise ValueError(
                    "workflow versions are immutable; create a new version"
                )
            return definition
        conn.execute(
            """
            INSERT INTO workflow_definitions(
                tenant_id, workspace_id, profile_id, workflow_id, version,
                source, read_only, definition_json, checksum, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                definition.tenant_id,
                definition.workspace_id,
                definition.profile_id,
                definition.id,
                definition.version,
                definition.source,
                int(definition.read_only),
                raw,
                checksum,
                self._now(),
            ),
        )
        return definition

    def put_definition(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        """Install one immutable definition version; identical writes are idempotent."""

        with self._connection(write=True) as conn:
            return self._put_definition_locked(conn, definition)

    def _install_builtin_definition(
        self, definition: WorkflowDefinition
    ) -> WorkflowDefinition:
        """Install a builtin without rewriting an older immutable snapshot.

        Builtin definitions live in source control, so their behavior can evolve
        while existing databases still contain an older checksum at the same
        source version. Preserve that snapshot and publish the changed builtin
        as the next version instead of making startup fail or mutating history.
        """

        checksum = self._definition_checksum(definition)
        with self._connection(write=True) as conn:
            try:
                return self._put_definition_locked(conn, definition)
            except ValueError as error:
                if str(error) != "workflow versions are immutable; create a new version":
                    raise

            exact = conn.execute(
                """
                SELECT version FROM workflow_definitions
                WHERE tenant_id = ? AND workspace_id = ? AND profile_id = ?
                  AND workflow_id = ? AND checksum = ?
                ORDER BY version DESC LIMIT 1
                """,
                (
                    definition.tenant_id,
                    definition.workspace_id,
                    definition.profile_id,
                    definition.id,
                    checksum,
                ),
            ).fetchone()
            if exact is not None:
                return definition.model_copy(update={"version": int(exact["version"])})

            existing = conn.execute(
                """
                SELECT version, source FROM workflow_definitions
                WHERE tenant_id = ? AND workspace_id = ? AND profile_id = ?
                  AND workflow_id = ? AND version = ?
                """,
                (
                    definition.tenant_id,
                    definition.workspace_id,
                    definition.profile_id,
                    definition.id,
                    definition.version,
                ),
            ).fetchone()
            if existing is None or existing["source"] != "builtin":
                raise ValueError(
                    "workflow versions are immutable; create a new version"
                )
            latest = conn.execute(
                """
                SELECT COALESCE(MAX(version), 0) AS version
                FROM workflow_definitions
                WHERE tenant_id = ? AND workspace_id = ? AND profile_id = ?
                  AND workflow_id = ?
                """,
                (
                    definition.tenant_id,
                    definition.workspace_id,
                    definition.profile_id,
                    definition.id,
                ),
            ).fetchone()
            next_version = max(int(latest["version"]), definition.version) + 1

            upgraded = definition.model_copy(update={"version": next_version})
            self._put_definition_locked(conn, upgraded)
            self._copy_builtin_override_locked(conn, definition, upgraded)
            return upgraded

    def _copy_builtin_override_locked(
        self,
        conn: sqlite3.Connection,
        previous: WorkflowDefinition,
        current: WorkflowDefinition,
    ) -> None:
        """Carry a prior builtin's user override onto its replacement version."""

        row = conn.execute(
            """
            SELECT enabled, priority, created_at, updated_at
            FROM workflow_overrides
            WHERE tenant_id = ? AND workspace_id = ? AND profile_id = ?
              AND workflow_id = ? AND workflow_version = ?
            """,
            (
                previous.tenant_id,
                previous.workspace_id,
                previous.profile_id,
                previous.id,
                previous.version,
            ),
        ).fetchone()
        if row is None:
            return
        now = self._now()
        conn.execute(
            """
            INSERT OR IGNORE INTO workflow_overrides(
                tenant_id, workspace_id, profile_id, workflow_id,
                workflow_version, enabled, priority, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                current.tenant_id,
                current.workspace_id,
                current.profile_id,
                current.id,
                current.version,
                row["enabled"],
                row["priority"],
                row["created_at"] or now,
                now,
            ),
        )

    def ensure_builtins(
        self,
        *,
        workspace_id: str = "local-default",
        profile_id: str = "local-default",
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> None:
        """Install the immutable built-in catalog in one workspace scope."""

        if tenant_id is None and scope is None:
            tenant_id = self.tenant_for_workspace(workspace_id)
        tenant_id, workspace_id = self._scope_values(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

        for definition in get_builtin_workflows():
            scoped = WorkflowDefinition.model_validate(
                {
                    **definition.model_dump(mode="python"),
                    "workspace_id": workspace_id,
                    "profile_id": profile_id,
                    "tenant_id": tenant_id,
                }
            )
            self._install_builtin_definition(scoped)

    def set_override(self, override: WorkflowOverride) -> WorkflowOverride:
        """Upsert activation/priority settings without changing definition content."""
        now = self._now()
        with self._connection(write=True) as conn:
            self._validate_workspace_tenant(
                conn, override.workspace_id, override.tenant_id
            )
            exists = conn.execute(
                """
                SELECT 1 FROM workflow_definitions
                WHERE tenant_id = ? AND workspace_id = ? AND profile_id = ?
                  AND workflow_id = ? AND version = ?
                """,
                (
                    override.tenant_id,
                    override.workspace_id,
                    override.profile_id,
                    override.workflow_id,
                    override.workflow_version,
                ),
            ).fetchone()
            if exists is None:
                raise KeyError("unknown workflow definition version")
            conflicting = conn.execute(
                """
                SELECT tenant_id FROM workflow_overrides
                WHERE workspace_id = ? AND profile_id = ?
                  AND workflow_id = ? AND workflow_version = ?
                """,
                (
                    override.workspace_id,
                    override.profile_id,
                    override.workflow_id,
                    override.workflow_version,
                ),
            ).fetchone()
            if conflicting is not None and conflicting["tenant_id"] != override.tenant_id:
                raise ValueError("workflow override belongs to another tenant")
            conn.execute(
                """
                INSERT INTO workflow_overrides(
                    tenant_id, workspace_id, profile_id, workflow_id, workflow_version,
                    enabled, priority, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    workspace_id, profile_id, workflow_id, workflow_version
                ) DO UPDATE SET
                    enabled = excluded.enabled,
                    priority = excluded.priority,
                    updated_at = excluded.updated_at
                """,
                (
                    override.tenant_id,
                    override.workspace_id,
                    override.profile_id,
                    override.workflow_id,
                    override.workflow_version,
                    None if override.enabled is None else int(override.enabled),
                    override.priority,
                    now,
                    now,
                ),
            )
        return override

    def clear_override(
        self,
        workflow_id: str,
        *,
        version: int,
        workspace_id: str = "local-default",
        profile_id: str = "local-default",
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> bool:
        """Remove a user override and restore the immutable definition defaults."""
        resolved_tenant, resolved_workspace = self._scope_values(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        workspace_id = resolved_workspace
        tenant_id = resolved_tenant
        with self._connection(write=True) as conn:
            cursor = conn.execute(
                """
                DELETE FROM workflow_overrides
                WHERE tenant_id = ? AND workspace_id = ? AND profile_id = ?
                  AND workflow_id = ? AND workflow_version = ?
                """,
                (
                    tenant_id,
                    workspace_id,
                    profile_id,
                    workflow_id,
                    version,
                ),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _resolved_definition(row: sqlite3.Row) -> WorkflowDefinition:
        definition = WorkflowDefinition.model_validate_json(row["definition_json"])
        definition = definition.model_copy(
            update={
                "tenant_id": row["tenant_id"],
                "workspace_id": row["workspace_id"],
                "profile_id": row["profile_id"],
            }
        )
        updates: dict[str, Any] = {}
        if row["override_enabled"] is not None:
            updates["enabled"] = bool(row["override_enabled"])
        if row["override_priority"] is not None:
            updates["priority"] = int(row["override_priority"])
        if not updates:
            return definition
        return WorkflowDefinition.model_validate(
            {**definition.model_dump(mode="python"), **updates}
        )

    def get_definition(
        self,
        workflow_id: str,
        *,
        version: int | None = None,
        workspace_id: str = "local-default",
        profile_id: str = "local-default",
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowDefinition | None:
        if tenant_id is None and scope is None:
            tenant_id = self.tenant_for_workspace(workspace_id)
        tenant_id, workspace_id = self._scope_values(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        version_sql = "AND d.version = ?" if version is not None else ""
        params: list[Any] = [tenant_id, workspace_id, profile_id, workflow_id]
        if version is not None:
            params.append(version)
        with self._connection() as conn:
            row = conn.execute(
                f"""
                SELECT d.definition_json, d.tenant_id, d.workspace_id, d.profile_id,
                       o.enabled AS override_enabled,
                       o.priority AS override_priority
                FROM workflow_definitions d
                LEFT JOIN workflow_overrides o
                  ON o.tenant_id = d.tenant_id
                 AND o.workspace_id = d.workspace_id
                 AND o.profile_id = d.profile_id
                 AND o.workflow_id = d.workflow_id
                 AND o.workflow_version = d.version
                WHERE d.tenant_id = ? AND d.workspace_id = ? AND d.profile_id = ?
                  AND d.workflow_id = ? {version_sql}
                ORDER BY d.version DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        return self._resolved_definition(row) if row is not None else None

    def list_definitions(
        self,
        *,
        workspace_id: str = "local-default",
        profile_id: str = "local-default",
        latest_only: bool = True,
        enabled_only: bool = False,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> list[WorkflowDefinition]:
        if tenant_id is None and scope is None:
            tenant_id = self.tenant_for_workspace(workspace_id)
        tenant_id, workspace_id = self._scope_values(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT d.definition_json, d.tenant_id, d.workspace_id, d.profile_id,
                       o.enabled AS override_enabled,
                       o.priority AS override_priority
                FROM workflow_definitions d
                LEFT JOIN workflow_overrides o
                  ON o.tenant_id = d.tenant_id
                 AND o.workspace_id = d.workspace_id
                 AND o.profile_id = d.profile_id
                 AND o.workflow_id = d.workflow_id
                 AND o.workflow_version = d.version
                WHERE d.tenant_id = ? AND d.workspace_id = ? AND d.profile_id = ?
                ORDER BY d.workflow_id, d.version DESC
                """,
                (tenant_id, workspace_id, profile_id),
            ).fetchall()
        definitions: list[WorkflowDefinition] = []
        seen: set[str] = set()
        for row in rows:
            definition = self._resolved_definition(row)
            if latest_only and definition.id in seen:
                continue
            seen.add(definition.id)
            if enabled_only and not definition.enabled:
                continue
            definitions.append(definition)
        return definitions

    def _event(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO workflow_events(
                run_id, tenant_id, event_type, payload_json, created_at
            )
            SELECT ?, tenant_id, ?, ?, ? FROM workflow_runs WHERE id = ?
            """,
            (
                run_id,
                event_type,
                self._json(payload or {}),
                self._now(),
                run_id,
            ),
        )

    def create_run(
        self,
        definition: WorkflowDefinition,
        conversation_id: str,
        *,
        input_data: dict[str, Any] | None = None,
        context_data: dict[str, Any] | None = None,
        turn_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> WorkflowRun:
        """Create a run and its steps once for a scoped idempotency key."""
        installed = self.get_definition(
            definition.id,
            version=definition.version,
            workspace_id=definition.workspace_id,
            profile_id=definition.profile_id,
            tenant_id=definition.tenant_id,
        )
        if installed is None:
            raise KeyError("workflow definition is not installed")
        if self._definition_checksum(installed) != self._definition_checksum(
            definition
        ):
            raise ValueError("resolved workflow does not match installed definition")

        input_data = dict(input_data or {})
        context_data = dict(context_data or {})
        input_json = self._json(input_data)
        context_json = self._json(context_data)
        snapshot_json = self._definition_json(definition)
        idempotency_key = idempotency_key or uuid4().hex
        run_id = uuid4().hex
        now = self._now()

        with self._connection(write=True) as conn:
            self._validate_workspace_tenant(
                conn, definition.workspace_id, definition.tenant_id
            )
            existing = conn.execute(
                """
                SELECT * FROM workflow_runs
                WHERE tenant_id = ? AND workspace_id = ? AND profile_id = ?
                  AND idempotency_key = ?
                """,
                (
                    definition.tenant_id,
                    definition.workspace_id,
                    definition.profile_id,
                    idempotency_key,
                ),
            ).fetchone()
            if existing is not None:
                if (
                    existing["workflow_id"] != definition.id
                    or int(existing["workflow_version"]) != definition.version
                    or existing["conversation_id"] != conversation_id
                    or existing["input_json"] != input_json
                    or existing["context_json"] != context_json
                ):
                    raise ValueError("idempotency key was used with different inputs")
                return self._run_from_row(existing)

            conversation = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND workspace_id = ?",
                (conversation_id, definition.workspace_id),
            ).fetchone()
            if conversation is None:
                raise KeyError(f"unknown conversation: {conversation_id}")
            conn.execute(
                """
                INSERT INTO workflow_runs(
                    id, idempotency_key, workflow_id, workflow_version,
                    tenant_id, workspace_id, profile_id, conversation_id, turn_id,
                    status, state_version, current_step,
                    definition_snapshot_json, input_json, context_json,
                    outputs_json, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0, ?, ?, ?, '{}', NULL, ?, ?)
                """,
                (
                    run_id,
                    idempotency_key,
                    definition.id,
                    definition.version,
                    definition.tenant_id,
                    definition.workspace_id,
                    definition.profile_id,
                    conversation_id,
                    turn_id,
                    snapshot_json,
                    input_json,
                    context_json,
                    now,
                    now,
                ),
            )
            for index, step in enumerate(definition.steps):
                conn.execute(
                    """
                    INSERT INTO workflow_steps(
                        run_id, step_index, tenant_id, step_id, skill_id, status,
                        state_version, attempt, input_json, output_json,
                        compensation_skill_id
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 0, 1, '{}', '{}', ?)
                    """,
                    (
                        run_id,
                        index,
                        definition.tenant_id,
                        step.id,
                        step.skill_id,
                        step.compensation_skill_id,
                    ),
                )
            self._event(
                conn,
                run_id,
                "run.created",
                {"workflow_id": definition.id, "version": definition.version},
            )
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return self._run_from_row(row)

    @classmethod
    def _run_from_row(cls, row: sqlite3.Row) -> WorkflowRun:
        return WorkflowRun(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            workflow_id=row["workflow_id"],
            workflow_version=int(row["workflow_version"]),
            workspace_id=row["workspace_id"],
            profile_id=row["profile_id"],
            tenant_id=row["tenant_id"],
            conversation_id=row["conversation_id"],
            turn_id=row["turn_id"],
            status=row["status"],
            state_version=int(row["state_version"]),
            current_step=int(row["current_step"]),
            definition_snapshot=WorkflowDefinition.model_validate_json(
                row["definition_snapshot_json"]
            ),
            input_data=cls._loads(row["input_json"], {}),
            context_data=cls._loads(row["context_json"], {}),
            outputs=cls._loads(row["outputs_json"], {}),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    @classmethod
    def _step_from_row(cls, row: sqlite3.Row) -> WorkflowStepRun:
        return WorkflowStepRun(
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            step_index=int(row["step_index"]),
            step_id=row["step_id"],
            skill_id=row["skill_id"],
            status=row["status"],
            state_version=int(row["state_version"]),
            attempt=int(row["attempt"]),
            input_data=cls._loads(row["input_json"], {}),
            output_data=cls._loads(row["output_json"], {}),
            error=row["error"],
            error_class=row["error_class"],
            next_retry_at=row["next_retry_at"],
            compensation_skill_id=row["compensation_skill_id"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> WorkflowApproval:
        return WorkflowApproval(
            id=row["id"],
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            step_index=int(row["step_index"]),
            requirement=row["requirement"],
            status=row["status"],
            actor=row["actor"],
            actor_level=row["actor_level"],
            note=row["note"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
        )

    def get_run(
        self,
        run_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowRun | None:
        tenant_clause = "" if tenant_id is None else " AND tenant_id = ?"
        workspace_clause = "" if workspace_id is None else " AND workspace_id = ?"
        if scope is not None:
            tenant_id, workspace_id = self._scope_values(scope=scope)
            tenant_clause = " AND tenant_id = ?"
            workspace_clause = " AND workspace_id = ?"
        elif tenant_id is None and workspace_id is not None:
            tenant_id = self.tenant_for_workspace(workspace_id)
            tenant_clause = " AND tenant_id = ?"
        parameters: tuple[Any, ...] = (run_id,)
        if tenant_id is not None:
            parameters += (tenant_id,)
        if workspace_id is not None:
            parameters += (workspace_id,)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ?"
                + tenant_clause
                + workspace_clause,
                parameters,
            ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def list_runs(
        self,
        conversation_id: str,
        *,
        statuses: tuple[RunStatus, ...] | None = None,
        workspace_id: str = "local-default",
        profile_id: str = "local-default",
        limit: int = 20,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> list[WorkflowRun]:
        """List recent runs for one scoped conversation."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
            raise ValueError("limit must be an integer between 1 and 100")
        allowed_statuses = {
            "pending",
            "awaiting_approval",
            "running",
            "succeeded",
            "failed",
            "cancelled",
        }
        requested = tuple(statuses or ())
        if any(status not in allowed_statuses for status in requested):
            raise ValueError("unsupported workflow run status")
        tenant_id, workspace_id = self._scope_values(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        status_sql = ""
        params: list[Any] = [conversation_id, tenant_id, workspace_id, profile_id]
        if requested:
            placeholders = ",".join("?" for _ in requested)
            status_sql = f" AND status IN ({placeholders})"
            params.extend(requested)
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM workflow_runs
                WHERE conversation_id = ? AND tenant_id = ?
                  AND workspace_id = ? AND profile_id = ?
                {status_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def clear_conversation_runs(
        self,
        conversation_id: str,
        *,
        workspace_id: str = "local-default",
        profile_id: str = "local-default",
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> int:
        """Delete runtime state when the user explicitly clears a conversation."""
        tenant_id, workspace_id = self._scope_values(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        with self._connection(write=True) as conn:
            cursor = conn.execute(
                """
                DELETE FROM workflow_runs
                WHERE conversation_id = ? AND tenant_id = ?
                  AND workspace_id = ? AND profile_id = ?
                """,
                (conversation_id, tenant_id, workspace_id, profile_id),
            )
        return cursor.rowcount

    def get_step(
        self,
        run_id: str,
        step_index: int,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowStepRun | None:
        tenant_clause = "" if tenant_id is None else " AND r.tenant_id = ?"
        workspace_clause = "" if workspace_id is None else " AND r.workspace_id = ?"
        if scope is not None:
            tenant_id, workspace_id = self._scope_values(scope=scope)
            tenant_clause = " AND r.tenant_id = ?"
            workspace_clause = " AND r.workspace_id = ?"
        parameters: tuple[Any, ...] = (run_id, step_index)
        if tenant_id is not None:
            parameters += (tenant_id,)
        if workspace_id is not None:
            parameters += (workspace_id,)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT s.* FROM workflow_steps s "
                "JOIN workflow_runs r ON r.id = s.run_id "
                "WHERE s.run_id = ? AND s.step_index = ?"
                + tenant_clause
                + workspace_clause,
                parameters,
            ).fetchone()
        return self._step_from_row(row) if row is not None else None

    def list_steps(
        self,
        run_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> list[WorkflowStepRun]:
        tenant_clause = "" if tenant_id is None else " AND r.tenant_id = ?"
        workspace_clause = "" if workspace_id is None else " AND r.workspace_id = ?"
        if scope is not None:
            tenant_id, workspace_id = self._scope_values(scope=scope)
            tenant_clause = " AND r.tenant_id = ?"
            workspace_clause = " AND r.workspace_id = ?"
        parameters: tuple[Any, ...] = (run_id,)
        if tenant_id is not None:
            parameters += (tenant_id,)
        if workspace_id is not None:
            parameters += (workspace_id,)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT s.* FROM workflow_steps s "
                "JOIN workflow_runs r ON r.id = s.run_id "
                "WHERE s.run_id = ?"
                + tenant_clause
                + workspace_clause
                + " ORDER BY s.step_index",
                parameters,
            ).fetchall()
        return [self._step_from_row(row) for row in rows]

    def list_events(
        self,
        run_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> list[WorkflowEvent]:
        tenant_clause = "" if tenant_id is None else " AND r.tenant_id = ?"
        workspace_clause = "" if workspace_id is None else " AND r.workspace_id = ?"
        if scope is not None:
            tenant_id, workspace_id = self._scope_values(scope=scope)
            tenant_clause = " AND r.tenant_id = ?"
            workspace_clause = " AND r.workspace_id = ?"
        parameters: tuple[Any, ...] = (run_id,)
        if tenant_id is not None:
            parameters += (tenant_id,)
        if workspace_id is not None:
            parameters += (workspace_id,)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT e.* FROM workflow_events e "
                "JOIN workflow_runs r ON r.id = e.run_id "
                "WHERE e.run_id = ?" + tenant_clause + workspace_clause + " ORDER BY e.id",
                parameters,
            ).fetchall()
        return [
            WorkflowEvent(
                id=int(row["id"]),
                run_id=row["run_id"],
                tenant_id=row["tenant_id"],
                event_type=row["event_type"],
                payload=self._loads(row["payload_json"], {}),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def transition_run(
        self,
        run_id: str,
        *,
        expected_status: RunStatus,
        expected_version: int,
        new_status: RunStatus,
        current_step: int | None = None,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowRun:
        """Compare-and-swap one run state transition."""
        tenant_id, workspace_id = self._mutation_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            scope=scope,
        )
        now = self._now()
        fields = ["status = ?", "state_version = state_version + 1", "updated_at = ?"]
        params: list[Any] = [new_status, now]
        if current_step is not None:
            fields.append("current_step = ?")
            params.append(current_step)
        if outputs is not None:
            fields.append("outputs_json = ?")
            params.append(self._json(outputs))
        if error is not None:
            fields.append("error = ?")
            params.append(error)
        if new_status == "running":
            fields.append("started_at = COALESCE(started_at, ?)")
            params.append(now)
        if new_status in {"succeeded", "failed", "cancelled"}:
            fields.append("completed_at = ?")
            params.append(now)
        params.extend([run_id, tenant_id])
        if workspace_id is not None:
            params.append(workspace_id)
        params.extend([expected_status, expected_version])
        workspace_clause = " AND workspace_id = ?" if workspace_id is not None else ""
        with self._connection(write=True) as conn:
            cursor = conn.execute(
                f"""
                UPDATE workflow_runs SET {", ".join(fields)}
                WHERE id = ? AND tenant_id = ?{workspace_clause}
                  AND status = ? AND state_version = ?
                """,
                params,
            )
            if cursor.rowcount != 1:
                raise WorkflowConflictError("run state changed concurrently")
            self._event(
                conn,
                run_id,
                "run.transitioned",
                {"from": expected_status, "to": new_status},
            )
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + workspace_clause,
                (run_id, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ()),
            ).fetchone()
        return self._run_from_row(row)

    def claim_step(
        self,
        run_id: str,
        step_index: int,
        input_data: dict[str, Any],
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        scope: Scope | None = None,
    ) -> tuple[WorkflowRun, WorkflowStepRun]:
        """Atomically claim a pending step so concurrent engines cannot duplicate it."""
        tenant_id, workspace_id = self._mutation_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            scope=scope,
        )
        now = self._now()
        input_json = self._json(input_data)
        workspace_clause = " AND r.workspace_id = ?" if workspace_id is not None else ""
        run_workspace_clause = (
            " AND workspace_id = ?" if workspace_id is not None else ""
        )
        run_parameters: tuple[Any, ...] = (run_id, tenant_id)
        if workspace_id is not None:
            run_parameters += (workspace_id,)
        with self._connection(write=True) as conn:
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
            step = conn.execute(
                "SELECT s.* FROM workflow_steps s "
                "JOIN workflow_runs r ON r.id = s.run_id "
                "WHERE s.run_id = ? AND s.step_index = ? AND r.tenant_id = ?"
                + workspace_clause,
                (run_id, step_index, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ()),
            ).fetchone()
            if run is None or step is None:
                raise KeyError("unknown run or step")
            if run["status"] not in {"pending", "running"}:
                raise WorkflowConflictError("run is not executable")
            if int(run["current_step"]) != step_index or step["status"] != "pending":
                raise WorkflowConflictError("step is not claimable")
            step_cursor = conn.execute(
                """
                UPDATE workflow_steps
                SET status = 'running', state_version = state_version + 1,
                    input_json = ?, error = NULL, error_class = NULL,
                    next_retry_at = NULL, started_at = ?, completed_at = NULL
                WHERE run_id = ? AND tenant_id = ? AND step_index = ?
                  AND status = 'pending' AND state_version = ?
                """,
                (
                    input_json,
                    now,
                    run_id,
                    tenant_id,
                    step_index,
                    int(step["state_version"]),
                ),
            )
            run_cursor = conn.execute(
                f"""
                UPDATE workflow_runs
                SET status = 'running', state_version = state_version + 1,
                    updated_at = ?, started_at = COALESCE(started_at, ?)
                WHERE id = ? AND tenant_id = ?{run_workspace_clause}
                  AND state_version = ? AND status IN ('pending', 'running')
                """,
                (now, now, run_id, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ())
                + (int(run["state_version"]),),
            )
            if step_cursor.rowcount != 1 or run_cursor.rowcount != 1:
                raise WorkflowConflictError("step claim lost a compare-and-swap race")
            self._event(conn, run_id, "step.started", {"step_index": step_index})
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
            step = conn.execute(
                "SELECT s.* FROM workflow_steps s "
                "JOIN workflow_runs r ON r.id = s.run_id "
                "WHERE s.run_id = ? AND s.step_index = ? AND r.tenant_id = ?"
                + workspace_clause,
                (run_id, step_index, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ()),
            ).fetchone()
        return self._run_from_row(run), self._step_from_row(step)

    def complete_step(
        self,
        run_id: str,
        step_index: int,
        output_key: str,
        output_data: dict[str, Any],
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowRun:
        """Persist output and atomically advance to the next step or success."""
        tenant_id, workspace_id = self._mutation_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            scope=scope,
        )
        output_json = self._json(output_data)
        now = self._now()
        workspace_clause = " AND r.workspace_id = ?" if workspace_id is not None else ""
        run_workspace_clause = (
            " AND workspace_id = ?" if workspace_id is not None else ""
        )
        run_parameters: tuple[Any, ...] = (run_id, tenant_id)
        if workspace_id is not None:
            run_parameters += (workspace_id,)
        with self._connection(write=True) as conn:
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
            step = conn.execute(
                "SELECT s.* FROM workflow_steps s "
                "JOIN workflow_runs r ON r.id = s.run_id "
                "WHERE s.run_id = ? AND s.step_index = ? AND r.tenant_id = ?"
                + workspace_clause,
                (run_id, step_index, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ()),
            ).fetchone()
            if run is None or step is None:
                raise KeyError("unknown run or step")
            if run["status"] != "running" or step["status"] != "running":
                raise WorkflowConflictError("step is not running")
            outputs = self._loads(run["outputs_json"], {})
            outputs[output_key] = output_data
            definition = WorkflowDefinition.model_validate_json(
                run["definition_snapshot_json"]
            )
            next_step = step_index + 1
            final = next_step >= len(definition.steps)
            next_status = "succeeded" if final else "pending"
            step_cursor = conn.execute(
                """
                UPDATE workflow_steps
                SET status = 'succeeded', state_version = state_version + 1,
                    output_json = ?, completed_at = ?
                WHERE run_id = ? AND tenant_id = ? AND step_index = ?
                  AND status = 'running' AND state_version = ?
                """,
                (
                    output_json,
                    now,
                    run_id,
                    tenant_id,
                    step_index,
                    int(step["state_version"]),
                ),
            )
            run_cursor = conn.execute(
                f"""
                UPDATE workflow_runs
                SET status = ?, state_version = state_version + 1,
                    current_step = ?, outputs_json = ?, updated_at = ?,
                    completed_at = {"?" if final else "completed_at"}
                WHERE id = ? AND tenant_id = ?{run_workspace_clause}
                  AND status = 'running' AND state_version = ?
                """,
                (
                    *(
                        [next_status, next_step, self._json(outputs), now, now]
                        if final
                        else [next_status, next_step, self._json(outputs), now]
                    ),
                    run_id,
                    tenant_id,
                    *((workspace_id,) if workspace_id is not None else ()),
                    int(run["state_version"]),
                ),
            )
            if step_cursor.rowcount != 1 or run_cursor.rowcount != 1:
                raise WorkflowConflictError("step completion lost a race")
            self._event(
                conn,
                run_id,
                "step.succeeded",
                {"step_index": step_index, "output_key": output_key},
            )
            if final:
                self._event(conn, run_id, "run.succeeded")
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
        return self._run_from_row(row)

    def schedule_retry(
        self,
        run_id: str,
        step_index: int,
        error: str,
        *,
        error_class: RetryErrorClass = "transient",
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        scope: Scope | None = None,
        backoff_seconds: float = 0.0,
    ) -> WorkflowRun:
        """Persist a bounded retry without replaying an unsafe side effect."""

        if error_class not in {"transient", "timeout", "network", "rate_limit", "unknown"}:
            raise ValueError("unsupported workflow error class")
        if (
            isinstance(backoff_seconds, bool)
            or not isinstance(backoff_seconds, (int, float))
            or backoff_seconds < 0
        ):
            raise ValueError("backoff_seconds must be a non-negative number")
        tenant_id, workspace_id = self._mutation_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            scope=scope,
        )
        now = datetime.now(timezone.utc)
        next_retry_at = (now + timedelta(seconds=float(backoff_seconds))).isoformat(
            timespec="microseconds"
        )
        now_text = now.isoformat(timespec="microseconds")
        run_workspace_clause = (
            " AND workspace_id = ?" if workspace_id is not None else ""
        )
        run_parameters: tuple[Any, ...] = (run_id, tenant_id)
        if workspace_id is not None:
            run_parameters += (workspace_id,)
        with self._connection(write=True) as conn:
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
            step = conn.execute(
                "SELECT s.* FROM workflow_steps s "
                "JOIN workflow_runs r ON r.id = s.run_id "
                "WHERE s.run_id = ? AND s.step_index = ? AND r.tenant_id = ?"
                + (" AND r.workspace_id = ?" if workspace_id is not None else ""),
                (run_id, step_index, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ()),
            ).fetchone()
            if run is None or step is None:
                raise KeyError("unknown run or step")
            if run["status"] != "running" or step["status"] != "running":
                raise WorkflowConflictError("only a running step can be retried")
            definition = WorkflowDefinition.model_validate_json(
                run["definition_snapshot_json"]
            )
            step_definition = definition.steps[step_index]
            if (
                step_definition.on_error != "retry"
                or not step_definition.retryable
                or not step_definition.idempotent
                or int(step["attempt"]) > step_definition.max_retries
                or error_class not in step_definition.retry_on
            ):
                raise WorkflowConflictError("step has no valid retry contract")
            next_attempt = int(step["attempt"]) + 1
            cursor = conn.execute(
                """
                UPDATE workflow_steps
                SET status = 'pending', state_version = state_version + 1,
                    attempt = ?, error = ?, error_class = ?, next_retry_at = ?,
                    completed_at = NULL
                WHERE run_id = ? AND tenant_id = ? AND step_index = ?
                  AND status = 'running' AND state_version = ?
                """,
                (
                    next_attempt,
                    error,
                    error_class,
                    next_retry_at,
                    run_id,
                    tenant_id,
                    step_index,
                    int(step["state_version"]),
                ),
            )
            if cursor.rowcount != 1:
                raise WorkflowConflictError("retry scheduling lost a race")
            run_cursor = conn.execute(
                f"""
                UPDATE workflow_runs
                SET status = 'pending', state_version = state_version + 1,
                    error = ?, updated_at = ?, completed_at = NULL
                WHERE id = ? AND tenant_id = ?{run_workspace_clause}
                  AND status = 'running' AND state_version = ?
                """,
                (error, now_text, run_id, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ())
                + (int(run["state_version"]),),
            )
            if run_cursor.rowcount != 1:
                raise WorkflowConflictError("retry run transition lost a race")
            self._event(
                conn,
                run_id,
                "step.retry_scheduled",
                {
                    "step_index": step_index,
                    "attempt": next_attempt,
                    "error_class": error_class,
                    "next_retry_at": next_retry_at,
                },
            )
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
        return self._run_from_row(row)

    def recover_stale_run(
        self,
        run_id: str,
        *,
        stale_after_seconds: float,
        allow_stale_retry: bool = False,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowRun | None:
        """Recover or stop a run whose claimed step outlived its lease.

        A stale read-only step is reset only when its definition explicitly
        declares a bounded idempotent retry contract and the caller grants a
        read-only recovery lease. Side-effecting steps are terminal and emit
        a compensation event instead of being replayed.
        """

        if (
            isinstance(stale_after_seconds, bool)
            or not isinstance(stale_after_seconds, (int, float))
            or stale_after_seconds < 0
        ):
            raise ValueError("stale_after_seconds must be a non-negative number")
        tenant_id, workspace_id = self._mutation_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            scope=scope,
        )
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="microseconds")
        run_workspace_clause = (
            " AND workspace_id = ?" if workspace_id is not None else ""
        )
        run_parameters: tuple[Any, ...] = (run_id, tenant_id)
        if workspace_id is not None:
            run_parameters += (workspace_id,)
        with self._connection(write=True) as conn:
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
            if run is None:
                raise KeyError("unknown run")
            if run["status"] != "running":
                return None
            step = conn.execute(
                "SELECT s.* FROM workflow_steps s "
                "JOIN workflow_runs r ON r.id = s.run_id "
                "WHERE s.run_id = ? AND s.step_index = ? AND r.tenant_id = ?"
                + (" AND r.workspace_id = ?" if workspace_id is not None else ""),
                (run_id, int(run["current_step"]), tenant_id)
                + ((workspace_id,) if workspace_id is not None else ()),
            ).fetchone()
            if step is None or step["status"] != "running":
                raise WorkflowConflictError("running workflow has no active step")
            lease_started = step["started_at"] or run["updated_at"]
            try:
                started_at = datetime.fromisoformat(str(lease_started).replace("Z", "+00:00"))
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return None
            if (now - started_at).total_seconds() < float(stale_after_seconds):
                return None

            definition = WorkflowDefinition.model_validate_json(
                run["definition_snapshot_json"]
            )
            step_definition = definition.steps[int(run["current_step"])]
            retry_available = (
                allow_stale_retry
                and not DEFAULT_RISK_POLICY.is_side_effect(
                    step_definition.capability,
                    declared_side_effect=step_definition.side_effect,
                )
                and step_definition.on_error == "retry"
                and step_definition.retryable
                and step_definition.idempotent
                and int(step["attempt"]) <= step_definition.max_retries
                and "timeout" in step_definition.retry_on
            )
            if retry_available:
                next_attempt = int(step["attempt"]) + 1
                step_cursor = conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status = 'pending', state_version = state_version + 1,
                        attempt = ?, error = 'stale execution lease expired',
                        error_class = 'timeout', next_retry_at = ?,
                        completed_at = NULL
                    WHERE run_id = ? AND tenant_id = ? AND step_index = ?
                      AND status = 'running' AND state_version = ?
                    """,
                    (
                        next_attempt,
                        now_text,
                        run_id,
                        tenant_id,
                        int(run["current_step"]),
                        int(step["state_version"]),
                    ),
                )
                run_cursor = conn.execute(
                    f"""
                    UPDATE workflow_runs
                    SET status = 'pending', state_version = state_version + 1,
                        error = NULL, updated_at = ?, completed_at = NULL
                    WHERE id = ? AND tenant_id = ?{run_workspace_clause}
                      AND status = 'running' AND state_version = ?
                    """,
                    (now_text, run_id, tenant_id)
                    + ((workspace_id,) if workspace_id is not None else ())
                    + (int(run["state_version"]),),
                )
                if step_cursor.rowcount != 1 or run_cursor.rowcount != 1:
                    raise WorkflowConflictError("stale recovery lost a race")
                self._event(
                    conn,
                    run_id,
                    "step.recovered",
                    {
                        "step_index": int(run["current_step"]),
                        "attempt": next_attempt,
                        "reason": "stale_execution_lease",
                    },
                )
            else:
                error = "workflow step became stale; manual recovery is required"
                step_cursor = conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status = 'failed', state_version = state_version + 1,
                        error = ?, error_class = 'timeout', completed_at = ?
                    WHERE run_id = ? AND tenant_id = ? AND step_index = ?
                      AND status = 'running' AND state_version = ?
                    """,
                    (
                        error,
                        now_text,
                        run_id,
                        tenant_id,
                        int(run["current_step"]),
                        int(step["state_version"]),
                    ),
                )
                run_cursor = conn.execute(
                    f"""
                    UPDATE workflow_runs
                    SET status = 'failed', state_version = state_version + 1,
                        error = ?, updated_at = ?, completed_at = ?
                    WHERE id = ? AND tenant_id = ?{run_workspace_clause}
                      AND status = 'running' AND state_version = ?
                    """,
                    (error, now_text, now_text, run_id, tenant_id)
                    + ((workspace_id,) if workspace_id is not None else ())
                    + (int(run["state_version"]),),
                )
                if step_cursor.rowcount != 1 or run_cursor.rowcount != 1:
                    raise WorkflowConflictError("stale failure lost a race")
                self._event(
                    conn,
                    run_id,
                    "step.recovery_required",
                    {
                        "step_index": int(run["current_step"]),
                        "reason": "stale_execution_lease",
                        "compensation_skill_id": step["compensation_skill_id"],
                    },
                )
                if step["compensation_skill_id"]:
                    self._event(
                        conn,
                        run_id,
                        "compensation.required",
                        {
                            "step_index": int(run["current_step"]),
                            "skill_id": step["compensation_skill_id"],
                        },
                    )
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
        return self._run_from_row(row)

    def fail_step(
        self,
        run_id: str,
        step_index: int,
        error: str,
        *,
        error_class: RetryErrorClass = "unknown",
        compensation_skill_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowRun:
        """Fail a claimed step and its run without retrying side effects."""
        if error_class not in {"transient", "timeout", "network", "rate_limit", "unknown"}:
            raise ValueError("unsupported workflow error class")
        tenant_id, workspace_id = self._mutation_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            scope=scope,
        )
        now = self._now()
        workspace_clause = " AND r.workspace_id = ?" if workspace_id is not None else ""
        run_workspace_clause = (
            " AND workspace_id = ?" if workspace_id is not None else ""
        )
        run_parameters: tuple[Any, ...] = (run_id, tenant_id)
        if workspace_id is not None:
            run_parameters += (workspace_id,)
        with self._connection(write=True) as conn:
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
            step = conn.execute(
                "SELECT s.* FROM workflow_steps s "
                "JOIN workflow_runs r ON r.id = s.run_id "
                "WHERE s.run_id = ? AND s.step_index = ? AND r.tenant_id = ?"
                + workspace_clause,
                (run_id, step_index, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ()),
            ).fetchone()
            if run is None or step is None:
                raise KeyError("unknown run or step")
            if run["status"] not in {"pending", "running"}:
                raise WorkflowConflictError("run cannot fail from its current status")
            if step["status"] not in {"pending", "running"}:
                raise WorkflowConflictError("step cannot fail from its current status")
            step_cursor = conn.execute(
                """
                UPDATE workflow_steps
                SET status = 'failed', state_version = state_version + 1,
                    error = ?, error_class = ?, next_retry_at = NULL,
                    compensation_skill_id = ?, completed_at = ?
                WHERE run_id = ? AND tenant_id = ? AND step_index = ?
                  AND state_version = ?
                """,
                (
                    error,
                    error_class,
                    compensation_skill_id,
                    now,
                    run_id,
                    tenant_id,
                    step_index,
                    int(step["state_version"]),
                ),
            )
            if step_cursor.rowcount != 1:
                raise WorkflowConflictError("step failure lost a race")
            run_cursor = conn.execute(
                f"""
                UPDATE workflow_runs
                SET status = 'failed', state_version = state_version + 1,
                    error = ?, updated_at = ?, completed_at = ?
                WHERE id = ? AND tenant_id = ?{run_workspace_clause}
                  AND state_version = ?
                """,
                (error, now, now, run_id, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ())
                + (int(run["state_version"]),),
            )
            if run_cursor.rowcount != 1:
                raise WorkflowConflictError("run failure lost a race")
            self._event(
                conn,
                run_id,
                "step.failed",
                {
                    "step_index": step_index,
                    "error": error[:1000],
                    "error_class": error_class,
                    "compensation_skill_id": (
                        compensation_skill_id or step["compensation_skill_id"]
                    ),
                },
            )
            if compensation_skill_id or step["compensation_skill_id"]:
                self._event(
                    conn,
                    run_id,
                    "compensation.required",
                    {
                        "step_index": step_index,
                        "skill_id": compensation_skill_id or step["compensation_skill_id"],
                    },
                )
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
        return self._run_from_row(row)

    def request_approval(
        self,
        run_id: str,
        step_index: int,
        requirement: ApprovalRequirement,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowApproval:
        """Persist an approval gate and pause the run before Skill execution."""
        if requirement == "none":
            raise ValueError("none does not require an approval record")
        tenant_id, workspace_id = self._mutation_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            scope=scope,
        )
        now = self._now()
        workspace_clause = " AND r.workspace_id = ?" if workspace_id is not None else ""
        run_workspace_clause = (
            " AND workspace_id = ?" if workspace_id is not None else ""
        )
        run_parameters: tuple[Any, ...] = (run_id, tenant_id)
        if workspace_id is not None:
            run_parameters += (workspace_id,)
        with self._connection(write=True) as conn:
            existing = conn.execute(
                "SELECT a.* FROM workflow_approvals a "
                "JOIN workflow_runs r ON r.id = a.run_id "
                "WHERE a.run_id = ? AND a.step_index = ? AND a.requirement = ?"
                " AND r.tenant_id = ?" + workspace_clause,
                (run_id, step_index, requirement, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ()),
            ).fetchone()
            if existing is not None:
                return self._approval_from_row(existing)
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND tenant_id = ?"
                + run_workspace_clause,
                run_parameters,
            ).fetchone()
            step = conn.execute(
                "SELECT s.* FROM workflow_steps s "
                "JOIN workflow_runs r ON r.id = s.run_id "
                "WHERE s.run_id = ? AND s.step_index = ? AND r.tenant_id = ?"
                + workspace_clause,
                (run_id, step_index, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ()),
            ).fetchone()
            if run is None or step is None:
                raise KeyError("unknown run or step")
            if (
                run["status"] not in {"pending", "running"}
                or step["status"] != "pending"
            ):
                raise WorkflowConflictError("run is not ready for approval")
            approval_id = uuid4().hex
            conn.execute(
                """
                INSERT INTO workflow_approvals(
                    id, run_id, step_index, tenant_id, requirement, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (approval_id, run_id, step_index, run["tenant_id"], requirement, now),
            )
            step_cursor = conn.execute(
                """
                UPDATE workflow_steps
                SET status = 'awaiting_approval', state_version = state_version + 1
                WHERE run_id = ? AND tenant_id = ? AND step_index = ?
                  AND status = 'pending' AND state_version = ?
                """,
                (run_id, tenant_id, step_index, int(step["state_version"])),
            )
            if step_cursor.rowcount != 1:
                raise WorkflowConflictError("approval step transition lost a race")
            run_cursor = conn.execute(
                f"""
                UPDATE workflow_runs
                SET status = 'awaiting_approval', state_version = state_version + 1,
                    updated_at = ?
                WHERE id = ? AND tenant_id = ?{run_workspace_clause}
                  AND state_version = ? AND status IN ('pending', 'running')
                """,
                (now, run_id, tenant_id)
                + ((workspace_id,) if workspace_id is not None else ())
                + (int(run["state_version"]),),
            )
            if run_cursor.rowcount != 1:
                raise WorkflowConflictError("approval run transition lost a race")
            self._event(
                conn,
                run_id,
                "approval.requested",
                {"step_index": step_index, "requirement": requirement},
            )
            row = conn.execute(
                "SELECT * FROM workflow_approvals WHERE id = ? AND tenant_id = ?",
                (approval_id, tenant_id),
            ).fetchone()
        return self._approval_from_row(row)

    def get_approval(
        self,
        run_id: str,
        step_index: int,
        requirement: ApprovalRequirement,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowApproval | None:
        if scope is not None:
            tenant_id, workspace_id = self._scope_values(scope=scope)
        tenant_clause = "" if tenant_id is None else " AND r.tenant_id = ?"
        workspace_clause = "" if workspace_id is None else " AND r.workspace_id = ?"
        parameters: tuple[Any, ...] = (run_id, step_index, requirement)
        if tenant_id is not None:
            parameters += (tenant_id,)
        if workspace_id is not None:
            parameters += (workspace_id,)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT a.* FROM workflow_approvals a "
                "JOIN workflow_runs r ON r.id = a.run_id "
                "WHERE a.run_id = ? AND a.step_index = ? AND a.requirement = ?"
                + tenant_clause
                + workspace_clause,
                parameters,
            ).fetchone()
        return self._approval_from_row(row) if row is not None else None

    def list_approvals(
        self,
        run_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> list[WorkflowApproval]:
        """List approval records for a run in step order."""
        if scope is not None:
            tenant_id, workspace_id = self._scope_values(scope=scope)
        tenant_clause = "" if tenant_id is None else " AND r.tenant_id = ?"
        workspace_clause = "" if workspace_id is None else " AND r.workspace_id = ?"
        parameters: tuple[Any, ...] = (run_id,)
        if tenant_id is not None:
            parameters += (tenant_id,)
        if workspace_id is not None:
            parameters += (workspace_id,)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT a.* FROM workflow_approvals a "
                "JOIN workflow_runs r ON r.id = a.run_id "
                "WHERE a.run_id = ?"
                + tenant_clause
                + workspace_clause
                + " ORDER BY a.step_index, a.created_at, a.id",
                parameters,
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def decide_approval(
        self,
        approval_id: str,
        *,
        decision: ApprovalStatus,
        actor: str,
        actor_level: ApprovalRequirement,
        note: str | None = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowApproval:
        """CAS an approval decision and resume or fail the paused run."""
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        if not actor.strip():
            raise ValueError("actor is required")
        if scope is not None:
            tenant_id, workspace_id = self._scope_values(scope=scope)
        tenant_clause = "" if tenant_id is None else " AND r.tenant_id = ?"
        now = self._now()
        with self._connection(write=True) as conn:
            workspace_clause = "" if workspace_id is None else " AND r.workspace_id = ?"
            params: tuple[Any, ...] = (approval_id,)
            if tenant_id is not None:
                params += (tenant_id,)
            if workspace_id is not None:
                params += (workspace_id,)
            approval = conn.execute(
                "SELECT a.* FROM workflow_approvals a "
                "JOIN workflow_runs r ON r.id = a.run_id "
                "WHERE a.id = ?" + tenant_clause + workspace_clause,
                params,
            ).fetchone()
            if approval is None:
                raise KeyError("unknown approval")
            if approval["status"] != "pending":
                if approval["status"] == decision:
                    return self._approval_from_row(approval)
                raise WorkflowConflictError("approval was already decided")
            if (
                decision == "approved"
                and APPROVAL_RANK[actor_level] < APPROVAL_RANK[approval["requirement"]]
            ):
                raise PermissionError("actor approval level is below the requirement")

            cursor = conn.execute(
                """
                UPDATE workflow_approvals
                SET status = ?, actor = ?, actor_level = ?, note = ?, decided_at = ?
                WHERE id = ? AND tenant_id = ? AND status = 'pending'
                """,
                (
                    decision,
                    actor.strip(),
                    actor_level,
                    note,
                    now,
                    approval_id,
                    approval["tenant_id"],
                ),
            )
            if cursor.rowcount != 1:
                raise WorkflowConflictError("approval decision lost a race")
            run_id = approval["run_id"]
            step_index = int(approval["step_index"])
            if decision == "approved":
                conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status = 'pending', state_version = state_version + 1
                    WHERE run_id = ? AND step_index = ?
                      AND status = 'awaiting_approval'
                    """,
                    (run_id, step_index),
                )
                conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'pending', state_version = state_version + 1,
                        updated_at = ?
                    WHERE id = ? AND status = 'awaiting_approval'
                    """,
                    (now, run_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status = 'failed', state_version = state_version + 1,
                        error = 'approval rejected', completed_at = ?
                    WHERE run_id = ? AND step_index = ?
                      AND status = 'awaiting_approval'
                    """,
                    (now, run_id, step_index),
                )
                conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'failed', state_version = state_version + 1,
                        error = 'approval rejected', updated_at = ?, completed_at = ?
                    WHERE id = ? AND status = 'awaiting_approval'
                    """,
                    (now, now, run_id),
                )
            self._event(
                conn,
                run_id,
                f"approval.{decision}",
                {"step_index": step_index, "actor": actor.strip()},
            )
            row = conn.execute(
                "SELECT * FROM workflow_approvals WHERE id = ? AND tenant_id = ?",
                (approval_id, approval["tenant_id"]),
            ).fetchone()
        return self._approval_from_row(row)

    def cancel_run(
        self,
        run_id: str,
        *,
        expected_version: int,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        scope: Scope | None = None,
    ) -> WorkflowRun:
        tenant_id, workspace_id = self._mutation_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            scope=scope,
        )
        run = self.get_run(
            run_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if run is None:
            raise KeyError("unknown run")
        if run.status in {"succeeded", "failed", "cancelled"}:
            return run
        return self.transition_run(
            run_id,
            expected_status=run.status,
            expected_version=expected_version,
            new_status="cancelled",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    def count_rows(self, table: str) -> int:
        """Small diagnostics helper restricted to workflow-owned tables."""
        allowed = {
            "workflow_definitions",
            "workflow_overrides",
            "workflow_runs",
            "workflow_steps",
            "workflow_approvals",
            "workflow_events",
        }
        if table not in allowed:
            raise ValueError("unsupported workflow table")
        with self._connection() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
