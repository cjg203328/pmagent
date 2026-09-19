"""Durable outbox for asynchronous learning tail operations.

The learning tail (Feedback recording, Episode recording, Reflection,
Knowledge Consolidation, TencentDB Memory capture) is moved from synchronous
execution at the end of `run_turn()` to a durable outbox + background worker
pattern. This decouples user-facing latency from best-effort learning operations.

Design:
- Each tail operation becomes a row in `outbox_jobs` with tenant/workspace scope
    - Idempotency: `(tenant_id, workspace_id, turn_id, job_type)` is unique
- Failed jobs remain in the outbox with error tracking and retry backoff
- Worker polls for available jobs and executes them outside the request path

Schema:
- `id`: unique job identifier
- `tenant_id`, `workspace_id`: scope for authorization and isolation
- `turn_id`: the turn that triggered this job (idempotency key part 1)
- `job_type`: feedback | episode | reflection | consolidation | tencentdb_memory
- `payload`: JSON-encoded job-specific data
- `status`: pending | running | completed | failed
- `attempts`: retry counter
- `available_at`: next retry timestamp (for exponential backoff)
- `last_error_code`: error classification for observability
- `created_at`, `updated_at`: lifecycle timestamps
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from artpm_agent.memory.sqlite_manager import SQLiteManager
from artpm_agent.tenancy import TenantContextManager, WorkspaceAccessDenied
from artpm_agent.utils import generate_uuid


class JobStatus(str, Enum):
    """Outbox job lifecycle status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(str, Enum):
    """Learning tail job types."""

    FEEDBACK = "feedback"
    EPISODE = "episode"
    REFLECTION = "reflection"
    CONSOLIDATION = "consolidation"
    TENCENTDB_MEMORY = "tencentdb_memory"


@dataclass
class OutboxJob:
    """A single durable outbox job."""

    id: str
    tenant_id: str
    workspace_id: str
    turn_id: str
    job_type: str
    payload: Dict[str, Any]
    status: str = JobStatus.PENDING.value
    attempts: int = 0
    available_at: str = ""
    last_error_code: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "turn_id": self.turn_id,
            "job_type": self.job_type,
            "payload": json.dumps(self.payload, ensure_ascii=False),
            "status": self.status,
            "attempts": self.attempts,
            "available_at": self.available_at or _utc_now(),
            "last_error_code": self.last_error_code or "",
            "created_at": self.created_at or _utc_now(),
            "updated_at": self.updated_at or _utc_now(),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _row_to_job(row: sqlite3.Row) -> OutboxJob:
    return OutboxJob(
        id=row["id"],
        tenant_id=row["tenant_id"],
        workspace_id=row["workspace_id"],
        turn_id=row["turn_id"],
        job_type=row["job_type"],
        payload=json.loads(row["payload"]) if row["payload"] else {},
        status=row["status"],
        attempts=row["attempts"],
        available_at=row["available_at"],
        last_error_code=row["last_error_code"] or None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class OutboxStore:
    """Persist and query outbox jobs for asynchronous learning operations."""

    def __init__(self, db_path: str):
        self.db = SQLiteManager(db_path, initialize_schema=False)
        self.db.remove_empty_primary_schema_scaffold()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.db.get_connection() as conn:
            existing = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'outbox_jobs'"
            ).fetchone()
            if existing and self._uses_legacy_unique_key(conn):
                self._migrate_scope_aware_unique_key(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox_jobs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    last_error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, workspace_id, turn_id, job_type)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_outbox_jobs_status_available
                ON outbox_jobs(status, available_at)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_outbox_jobs_scope
                ON outbox_jobs(tenant_id, workspace_id, created_at)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_outbox_jobs_turn
                ON outbox_jobs(turn_id)
                """
            )

    @staticmethod
    def _uses_legacy_unique_key(conn: sqlite3.Connection) -> bool:
        """Detect the old constraint through SQLite metadata, not SQL text."""
        for index in conn.execute("PRAGMA index_list('outbox_jobs')").fetchall():
            if not index["unique"]:
                continue
            columns = [
                row["name"]
                for row in conn.execute(
                    f"PRAGMA index_info('{str(index['name']).replace(chr(39), chr(39) * 2)}')"
                ).fetchall()
            ]
            if columns == ["turn_id", "job_type"]:
                return True
        return False

    @staticmethod
    def _migrate_scope_aware_unique_key(conn: sqlite3.Connection) -> None:
        """Replace the pre-scope unique constraint without losing jobs."""
        conn.execute("ALTER TABLE outbox_jobs RENAME TO outbox_jobs_legacy")
        conn.execute(
            """
            CREATE TABLE outbox_jobs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(tenant_id, workspace_id, turn_id, job_type)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO outbox_jobs(
                id, tenant_id, workspace_id, turn_id, job_type, payload,
                status, attempts, available_at, last_error_code, created_at, updated_at
            )
            SELECT id, tenant_id, workspace_id, turn_id, job_type, payload,
                   status, attempts, available_at, last_error_code, created_at, updated_at
            FROM outbox_jobs_legacy
            """
        )
        conn.execute("DROP TABLE outbox_jobs_legacy")

    @staticmethod
    def _resolve_scope(
        tenant_id: Optional[str],
        workspace_id: Optional[str],
    ) -> tuple[str, str]:
        """Resolve tenant/workspace scope with authentication."""
        current = TenantContextManager.get_current()
        if current is not None:
            requested_tenant = str(tenant_id or "").strip()
            requested_workspace = str(workspace_id or "").strip()
            if requested_tenant and requested_tenant != current.tenant_id:
                raise WorkspaceAccessDenied(
                    "tenant does not match the authenticated context"
                )
            if requested_workspace and requested_workspace != current.workspace_id:
                raise WorkspaceAccessDenied(
                    "workspace does not match the authenticated context"
                )
            return current.tenant_id, current.workspace_id
        return (
            str(tenant_id or "local").strip() or "local",
            str(workspace_id or "local-default").strip() or "local-default",
        )

    def enqueue(
        self,
        turn_id: str,
        job_type: JobType,
        payload: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> str:
        """Enqueue a job idempotently inside one tenant/workspace scope."""
        resolved_tenant, resolved_workspace = self._resolve_scope(
            tenant_id, workspace_id
        )
        job = OutboxJob(
            id=generate_uuid(),
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
            turn_id=turn_id,
            job_type=job_type.value,
            payload=payload,
        )
        row = job.to_row()
        with self.db.get_connection() as conn:
            # Idempotent inside the authenticated tenant/workspace scope.
            conn.execute(
                """
                INSERT OR IGNORE INTO outbox_jobs (
                    id, tenant_id, workspace_id, turn_id, job_type, payload,
                    status, attempts, available_at, last_error_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["tenant_id"],
                    row["workspace_id"],
                    row["turn_id"],
                    row["job_type"],
                    row["payload"],
                    row["status"],
                    row["attempts"],
                    row["available_at"],
                    row["last_error_code"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
            existing = conn.execute(
                """
                SELECT id, tenant_id, workspace_id
                FROM outbox_jobs
                WHERE tenant_id = ? AND workspace_id = ?
                  AND turn_id = ? AND job_type = ?
                """,
                (resolved_tenant, resolved_workspace, turn_id, job_type.value),
            ).fetchone()
        if existing is None:
            # The INSERT and lookup share one committed connection; reaching
            # this branch means the database contract was violated.
            raise RuntimeError("outbox enqueue did not persist a job")
        # Return the durable ID, including when INSERT OR IGNORE found an
        # existing idempotent job. Callers must never receive a phantom ID.
        return str(existing["id"])

    def poll(
        self,
        limit: int = 10,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> List[OutboxJob]:
        """Poll for available jobs (status=pending, available_at <= now)."""
        resolved_tenant, resolved_workspace = self._resolve_scope(
            tenant_id, workspace_id
        )
        now = _utc_now()
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM outbox_jobs
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND status = ?
                  AND available_at <= ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (
                    resolved_tenant,
                    resolved_workspace,
                    JobStatus.PENDING.value,
                    now,
                    limit,
                ),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def claim(
        self,
        limit: int = 10,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> List[OutboxJob]:
        """Atomically claim pending jobs for one worker.

        ``poll`` is retained as a read-only inspection API. Workers must use
        this method so two concurrent workers cannot both execute the same
        pending row between a SELECT and a status update.
        """
        resolved_tenant, resolved_workspace = self._resolve_scope(
            tenant_id, workspace_id
        )
        limit = max(1, int(limit))
        now = _utc_now()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM outbox_jobs
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND status = ?
                  AND available_at <= ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (
                    resolved_tenant,
                    resolved_workspace,
                    JobStatus.PENDING.value,
                    now,
                    limit,
                ),
            ).fetchall()
            jobs: list[OutboxJob] = []
            for row in rows:
                updated = conn.execute(
                    """
                    UPDATE outbox_jobs
                    SET status = ?, updated_at = ?
                    WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                      AND status = ?
                    """,
                    (
                        JobStatus.RUNNING.value,
                        now,
                        row["id"],
                        resolved_tenant,
                        resolved_workspace,
                        JobStatus.PENDING.value,
                    ),
                )
                if updated.rowcount:
                    job = _row_to_job(row)
                    job.status = JobStatus.RUNNING.value
                    job.updated_at = now
                    jobs.append(job)
        return jobs

    def mark_running(
        self,
        job_id: str,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """Mark a job as running."""
        scoped = (
            tenant_id is not None
            or workspace_id is not None
            or TenantContextManager.get_current() is not None
        )
        scope = self._resolve_scope(tenant_id, workspace_id) if scoped else None
        with self.db.get_connection() as conn:
            if scope is None:
                updated = conn.execute(
                    """
                    UPDATE outbox_jobs
                    SET status = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        JobStatus.RUNNING.value,
                        _utc_now(),
                        job_id,
                        JobStatus.PENDING.value,
                    ),
                )
            else:
                updated = conn.execute(
                    """
                    UPDATE outbox_jobs
                    SET status = ?, updated_at = ?
                    WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                      AND status = ?
                    """,
                    (
                        JobStatus.RUNNING.value,
                        _utc_now(),
                        job_id,
                        scope[0],
                        scope[1],
                        JobStatus.PENDING.value,
                    ),
                )
        return bool(updated.rowcount)

    def mark_completed(
        self,
        job_id: str,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """Mark a job as completed."""
        scoped = (
            tenant_id is not None
            or workspace_id is not None
            or TenantContextManager.get_current() is not None
        )
        scope = self._resolve_scope(tenant_id, workspace_id) if scoped else None
        with self.db.get_connection() as conn:
            if scope is None:
                updated = conn.execute(
                    """
                    UPDATE outbox_jobs
                    SET status = ?, updated_at = ?
                    WHERE id = ? AND status IN (?, ?)
                    """,
                    (
                        JobStatus.COMPLETED.value,
                        _utc_now(),
                        job_id,
                        JobStatus.PENDING.value,
                        JobStatus.RUNNING.value,
                    ),
                )
            else:
                updated = conn.execute(
                    """
                    UPDATE outbox_jobs
                    SET status = ?, updated_at = ?
                    WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                      AND status IN (?, ?)
                    """,
                    (
                        JobStatus.COMPLETED.value,
                        _utc_now(),
                        job_id,
                        scope[0],
                        scope[1],
                        JobStatus.PENDING.value,
                        JobStatus.RUNNING.value,
                    ),
                )
        return bool(updated.rowcount)

    def mark_failed(
        self,
        job_id: str,
        error_code: str,
        retry_after_seconds: int = 60,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """Mark a job as failed and schedule retry with exponential backoff."""
        scoped = (
            tenant_id is not None
            or workspace_id is not None
            or TenantContextManager.get_current() is not None
        )
        scope = self._resolve_scope(tenant_id, workspace_id) if scoped else None
        now = datetime.now(timezone.utc)
        available_at = (now + timedelta(seconds=retry_after_seconds)).isoformat(
            timespec="microseconds"
        )
        with self.db.get_connection() as conn:
            if scope is None:
                updated = conn.execute(
                    """
                    UPDATE outbox_jobs
                    SET status = ?,
                        attempts = attempts + 1,
                        last_error_code = ?,
                        available_at = ?,
                        updated_at = ?
                    WHERE id = ? AND status IN (?, ?)
                    """,
                    (
                        JobStatus.PENDING.value,
                        error_code,
                        available_at,
                        _utc_now(),
                        job_id,
                        JobStatus.PENDING.value,
                        JobStatus.RUNNING.value,
                    ),
                )
            else:
                updated = conn.execute(
                    """
                    UPDATE outbox_jobs
                    SET status = ?,
                        attempts = attempts + 1,
                        last_error_code = ?,
                        available_at = ?,
                        updated_at = ?
                    WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                      AND status IN (?, ?)
                    """,
                    (
                        JobStatus.PENDING.value,
                        error_code,
                        available_at,
                        _utc_now(),
                        job_id,
                        scope[0],
                        scope[1],
                        JobStatus.PENDING.value,
                        JobStatus.RUNNING.value,
                    ),
                )
        return bool(updated.rowcount)

    def get_by_turn(
        self,
        turn_id: str,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> List[OutboxJob]:
        """Retrieve all jobs for a given turn (for debugging/audit)."""
        resolved_tenant, resolved_workspace = self._resolve_scope(
            tenant_id, workspace_id
        )
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM outbox_jobs
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND turn_id = ?
                ORDER BY created_at ASC
                """,
                (resolved_tenant, resolved_workspace, turn_id),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def count_pending(
        self,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> int:
        """Count pending jobs (observability metric)."""
        resolved_tenant, resolved_workspace = self._resolve_scope(
            tenant_id, workspace_id
        )
        with self.db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM outbox_jobs
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND status = ?
                """,
                (resolved_tenant, resolved_workspace, JobStatus.PENDING.value),
            ).fetchone()
        return row["cnt"] if row else 0

    def close(self) -> None:
        """Close the underlying database connection."""
        # SQLiteManager doesn't have close(); connections are context-managed
        pass
